"""FTS5 디스크 색인 — 인메모리 BM25의 드롭인 교체 (SPEC §3 AC-R3~R5).

🔴 **왜 바꿨나 (실측)**
인메모리 BM25는 (doc,term) 쌍당 약 684 B를 쓴다. 6,504섹션에 561 MB이고, 쌍 수가
코퍼스에 선형이라 전량(~117K섹션)이면 **9.9 GB** — NCP 권장 서버 4 GB에 올라가지 않는다.
같은 색인을 FTS5로 만들면 6,504섹션 기준 **31 MB(디스크)** 이고 조회 시에만 페이지를
읽으므로 상주 메모리가 코퍼스 크기와 무관해진다.

🔴 **한국어 처리**
FTS5에 Kiwi 형태소 분석기를 직접 붙일 수 없다(커스텀 토크나이저는 C API 필요).
대신 **색인 시점에 Kiwi로 분해한 토큰을 공백으로 이어 붙여** 저장하고 `unicode61`로
색인한다. 질의도 같은 분해를 거치므로 형태소 단위 매칭이 유지된다.

⚠️ **k1/b는 조정할 수 없다.** FTS5 `bm25()`는 k1=1.2, b=0.75 고정이다. 기본값과
같으면 무해하지만 config에서 바꾸면 무시되므로 경고를 남긴다.
"""

from __future__ import annotations

import logging
import re
import sqlite3

from ..observability import question_id as hash_question
from .bm25 import Doc, SearchHit
from .tokenizer import Tokenizer, default_tokenizer

log = logging.getLogger(__name__)

FTS_TABLE = "section_fts"
_SCHEMA_KEY = "fts_tokenizer_mode"

# FTS5 질의 문법에서 의미를 갖는 문자 — 토큰에 섞이면 구문 오류를 낸다
_UNSAFE = re.compile(r'["\'\(\)\*\:\^\-]')


def _quote(token: str) -> str:
    """토큰을 FTS5 문자열 리터럴로 감싼다 (내부 큰따옴표는 중복으로 이스케이프)."""
    return '"' + token.replace('"', '""') + '"'


def _safe_tokens(tokens: list[str]) -> list[str]:
    out = []
    for t in tokens:
        t = _UNSAFE.sub(" ", t).strip()
        if t:
            out.append(t)
    return out


# ── 빌드 ────────────────────────────────────────────────────────────────────


def build_fts(
    conn: sqlite3.Connection, *, tokenizer: Tokenizer | None = None, limit: int = 0
) -> int:
    """section 테이블 → FTS5 색인. 선별 색인 정책은 bm25.load_index와 동일 (AC-R3).

    prose          → 제목 + 본문
    financial_stmt → 제목 + 표 markdown
    table_registry → 제목만 (볼륨 47%를 차지하는 인명·계열사 표 본문 제외)
    """
    tok = tokenizer or default_tokenizer()
    conn.execute(f"DROP TABLE IF EXISTS {FTS_TABLE}")
    conn.execute(
        f"CREATE VIRTUAL TABLE {FTS_TABLE} USING fts5(toks, tokenize='unicode61')"
    )
    conn.execute("CREATE TABLE IF NOT EXISTS index_meta (key TEXT PRIMARY KEY, value TEXT)")

    sql = (
        "SELECT s.rowid AS rid, s.title, s.text, s.tables_md, s.content_class "
        "FROM section s JOIN document d ON d.doc_id=s.doc_id WHERE d.is_effective=1"
    )
    if limit:
        sql += f" LIMIT {int(limit)}"

    n, batch = 0, []
    for r in conn.execute(sql):
        cls = r["content_class"]
        if cls == "table_registry":
            body = ""
        elif cls == "financial_stmt":
            body = (r["tables_md"] or "")[:20_000]
        else:
            body = (r["text"] or "")[:60_000]
        toks = _safe_tokens(tok.tokens(f"{r['title']} {body}"))
        batch.append((r["rid"], " ".join(toks)))
        n += 1
        if len(batch) >= 2_000:
            conn.executemany(f"INSERT INTO {FTS_TABLE}(rowid,toks) VALUES (?,?)", batch)
            batch.clear()
    if batch:
        conn.executemany(f"INSERT INTO {FTS_TABLE}(rowid,toks) VALUES (?,?)", batch)

    conn.execute(f"INSERT INTO {FTS_TABLE}({FTS_TABLE}) VALUES('optimize')")
    conn.execute("INSERT OR REPLACE INTO index_meta VALUES (?,?)", (_SCHEMA_KEY, tok.mode))
    conn.commit()
    return n


def fts_ready(conn: sqlite3.Connection, *, expect_tokenizer: str | None = None) -> bool:
    """색인 존재 + 토크나이저 일치 확인. 불일치면 토큰이 호환되지 않아 재빌드해야 한다."""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (FTS_TABLE,)
    ).fetchone()
    if not row:
        return False
    if expect_tokenizer:
        try:
            m = conn.execute(
                "SELECT value FROM index_meta WHERE key=?", (_SCHEMA_KEY,)
            ).fetchone()
        except sqlite3.Error:
            return False
        if not m or m[0] != expect_tokenizer:
            return False
    return bool(conn.execute(f"SELECT 1 FROM {FTS_TABLE} LIMIT 1").fetchone())


# ── 조회 ────────────────────────────────────────────────────────────────────


class FtsIndex:
    """BM25Index와 동일한 호출 계약을 갖는 디스크 색인 (드롭인 교체)."""

    def __init__(self, conn: sqlite3.Connection, tokenizer: Tokenizer | None = None):
        self.conn = conn
        self.tok = tokenizer or default_tokenizer()
        self._size = conn.execute(f"SELECT count(*) FROM {FTS_TABLE}").fetchone()[0]

    @property
    def size(self) -> int:
        return self._size

    def search(
        self,
        query: str,
        *,
        top_k: int = 30,
        corp_codes: set[str] | None = None,
        doc_groups: set[str] | None = None,
        years: set[int] | None = None,
        doc_ids: set[str] | None = None,
    ) -> list[SearchHit]:
        """필터를 SQL로 밀어넣고 FTS5 bm25()로 랭킹한다 (파이썬 후처리 없음)."""
        toks = _safe_tokens(self.tok.tokens(query))
        if not toks or not self._size:
            return []
        match = " OR ".join(_quote(t) for t in dict.fromkeys(toks))

        where, params = [f"{FTS_TABLE} MATCH ?", "d.is_effective=1"], [match]
        for col, values in (
            ("s.corp_code", corp_codes), ("d.doc_group", doc_groups),
            ("d.base_year", years), ("s.doc_id", doc_ids),
        ):
            if values:
                where.append(f"{col} IN ({','.join('?' * len(values))})")
                params.extend(values)
        params.append(top_k)

        sql = (
            "SELECT s.section_id,s.doc_id,s.corp_code,s.path,s.title,s.text,"
            f"       s.content_class,d.base_year,d.doc_group, bm25({FTS_TABLE}) AS sc "
            f"FROM {FTS_TABLE} f JOIN section s ON s.rowid=f.rowid "
            "JOIN document d ON d.doc_id=s.doc_id "
            f"WHERE {' AND '.join(where)} ORDER BY sc LIMIT ?"
        )
        try:
            rows = self.conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError as exc:  # 질의 구문 오류는 무검색으로 강등
            log.warning("FTS 질의 실패(%s) — query_hash=%s, 검색 결과 없음",
                        type(exc).__name__, hash_question(query))
            return []

        hits = []
        for r in rows:
            doc = Doc(
                doc_key=r["section_id"], doc_id=r["doc_id"], corp_code=r["corp_code"],
                path=r["path"], title=r["title"], text=r["text"] or "",
                content_class=r["content_class"], base_year=r["base_year"],
                doc_group=r["doc_group"],
            )
            # bm25()는 작을수록 관련도가 높다 → 부호를 뒤집어 BM25Index와 의미를 맞춘다
            hits.append(SearchHit(doc.doc_key, -float(r["sc"]), doc, ["bm25"]))
        return hits
