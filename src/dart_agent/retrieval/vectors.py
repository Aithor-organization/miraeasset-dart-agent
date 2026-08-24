"""벡터 스토어 — 하이브리드 검색 파일럿 (bge-m3, SPEC §3 AC-R4 벡터 팔).

🔴 **sidecar 파일에 저장한다** (기본 `index/embeddings.sqlite`).
   본 인덱스(dart.sqlite, 3.8 GB)는 운영 서버가 읽기 연결로 잡고 있다 —
   임베딩 적재가 그 파일에 쓰기 잠금을 걸면 안 된다.

🔴 **파일럿 커버리지는 부분이다.** 임베딩된 섹션만 벡터 팔에 뜬다. 커버리지
   밖 섹션은 BM25 팔로만 랭킹되므로, RRF 융합 결과는 커버 구간에 유리하게
   기운다. 파일럿 A/B는 커버 구간(골든셋 20개 기업 최신 사업보고서) 질문으로만
   판정할 것 — eval/retrieval_ab.py가 그 경계를 지킨다.

의존성: numpy가 있으면 행렬 내적(빠름), 없으면 순수 파이썬 폴백.
        requirements.txt에 numpy를 **추가하지 않았다** — 파일럿 판정 전까지
        운영 의존성을 늘리지 않는다 (폴백 실측: 2,918개 × 1024차원 ≈ 0.9s/질의).
"""

from __future__ import annotations

import sqlite3
import struct
from array import array
from pathlib import Path

from .bm25 import Doc, SearchHit

try:  # 선택 의존성 — 없어도 동작한다
    import numpy as _np
except ImportError:  # pragma: no cover
    _np = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS embedding (
  section_id TEXT PRIMARY KEY,
  corp_code  TEXT NOT NULL,
  doc_id     TEXT NOT NULL,
  path       TEXT NOT NULL,
  title      TEXT NOT NULL,
  base_year  INTEGER,
  model      TEXT NOT NULL,
  dim        INTEGER NOT NULL,
  vec        BLOB NOT NULL          -- float32 little-endian, L2 정규화 저장
);
CREATE INDEX IF NOT EXISTS ix_emb_corp ON embedding(corp_code);
"""


def open_store(path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def _normalize(vec: list[float]) -> array:
    a = array("f", vec)
    norm = sum(x * x for x in a) ** 0.5
    if norm > 0:
        for i in range(len(a)):
            a[i] /= norm
    return a


def save_vectors(conn: sqlite3.Connection, model: str, rows: list[tuple]) -> None:
    """rows: (section_id, corp_code, doc_id, path, title, base_year, vec[list[float]])"""
    payload = []
    for sid, corp, doc_id, path, title, year, vec in rows:
        a = _normalize(vec)
        payload.append((sid, corp, doc_id, path, title, year, model, len(a), a.tobytes()))
    conn.executemany(
        "INSERT OR REPLACE INTO embedding VALUES (?,?,?,?,?,?,?,?,?)", payload
    )
    conn.commit()


def existing_ids(conn: sqlite3.Connection, model: str) -> set[str]:
    return {r[0] for r in conn.execute(
        "SELECT section_id FROM embedding WHERE model=?", (model,))}


class VectorStore:
    """메모리 상주 코사인 검색. 파일럿 규모(수천~수만)까지 실용적.

    저장 시 L2 정규화했으므로 코사인 = 내적. 질의 벡터도 정규화해서 받는다.
    """

    def __init__(self, ids: list[str], meta: list[tuple], dim: int, model: str,
                 flat: array) -> None:
        self.ids = ids
        self.meta = meta          # (corp_code, doc_id, path, title, base_year)
        self.dim = dim
        self.model = model
        self._flat = flat         # len == len(ids) * dim
        self._mat = (_np.frombuffer(flat.tobytes(), dtype=_np.float32)
                     .reshape(len(ids), dim)) if (_np is not None and ids) else None

    @property
    def size(self) -> int:
        return len(self.ids)

    @classmethod
    def load(cls, path: Path | str, *, model: str | None = None) -> "VectorStore | None":
        """실패는 전부 None — 하이브리드는 선택 기능이라 서버 기동을 죽이면 안 된다.

        connect·파싱·무결성(모델 혼합/차원 혼합/BLOB 길이) 어느 실패든 None으로
        수렴시킨다 (Codex 리뷰 2026-08-24 — 손상 스토어가 기동 중단 유발 지적).
        """
        p = Path(path)
        if not p.exists():
            return None
        try:
            conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        except sqlite3.Error:
            return None
        try:
            conn.row_factory = sqlite3.Row
            sql = "SELECT * FROM embedding"
            args: tuple = ()
            if model:
                sql += " WHERE model=?"
                args = (model,)
            ids, meta, flat, dim, mdl = [], [], array("f"), 0, model or ""
            for r in conn.execute(sql, args):
                if dim == 0:
                    dim, mdl = r["dim"], r["model"]
                if r["dim"] != dim:      # 차원이 섞인 스토어는 신뢰 불가
                    return None
                if r["model"] != mdl:    # 🔴 모델 혼합 = 서로 다른 임베딩 공간
                    return None
                if len(r["vec"]) != dim * 4:  # float32 길이 불일치 = 손상 BLOB
                    return None
                ids.append(r["section_id"])
                meta.append((r["corp_code"], r["doc_id"], r["path"],
                             r["title"], r["base_year"]))
                flat.frombytes(r["vec"])
        except (sqlite3.Error, ValueError):
            return None
        finally:
            conn.close()
        return cls(ids, meta, dim, mdl, flat) if ids else None

    def search(self, qvec: list[float], *, top_k: int = 30,
               corp_codes: set[str] | None = None,
               years: set[int] | None = None) -> list[SearchHit]:
        """코사인 상위 top_k. Doc.text는 비워 반환한다 — 융합 후 승자만
        호출부(tools.doc_search)가 DB에서 본문을 채운다 (전 후보 적재 회피)."""
        if not self.ids or top_k <= 0:
            return []
        q = _normalize(list(qvec))
        if len(q) != self.dim:
            return []
        if self._mat is not None:
            qn = _np.frombuffer(q.tobytes(), dtype=_np.float32)
            sims = self._mat @ qn
            order = sims.argsort()[::-1]
            scored = ((int(i), float(sims[int(i)])) for i in order)
        else:  # 순수 파이썬 폴백
            d, f = self.dim, self._flat
            all_scored = []
            for i in range(len(self.ids)):
                base = i * d
                s = 0.0
                for j in range(d):
                    s += f[base + j] * q[j]
                all_scored.append((i, s))
            all_scored.sort(key=lambda t: -t[1])
            scored = iter(all_scored)

        out: list[SearchHit] = []
        for i, sim in scored:
            corp, doc_id, path, title, year = self.meta[i]
            if corp_codes and corp not in corp_codes:
                continue
            # 🔴 연도 필터 — BM25 팔과 동일하게 적용하지 않으면 연도 지정 질문에
            #    다른 연도 근거가 융합 결과로 끼어든다 (스토어는 최신본 위주)
            if years and year not in years:
                continue
            doc = Doc(doc_key=self.ids[i], doc_id=doc_id, corp_code=corp,
                      path=path, title=title, text="", content_class="",
                      base_year=year, doc_group="periodic")
            out.append(SearchHit(self.ids[i], sim, doc, ["vec"]))
            if len(out) >= top_k:
                break
        return out


def fetch_doc(conn: sqlite3.Connection, section_id: str, *,
              max_chars: int = 60_000) -> Doc | None:
    """벡터 팔 단독 승자의 본문 보강 (융합 top_k에 대해서만 호출)."""
    # is_effective=1 — 임베딩 이후 정정본이 반영돼 비유효가 된 문서는 인용 금지
    r = conn.execute(
        "SELECT s.section_id,s.doc_id,s.corp_code,s.path,s.title,s.text,s.tables_md,"
        "       s.content_class,d.base_year,d.doc_group "
        "FROM section s JOIN document d ON d.doc_id=s.doc_id "
        "WHERE s.section_id=? AND d.is_effective=1",
        (section_id,)).fetchone()
    if not r:
        return None
    body = r["text"] or ""
    if r["content_class"] in ("financial_stmt", "table_registry") and r["tables_md"]:
        body = r["tables_md"]
    return Doc(doc_key=r["section_id"], doc_id=r["doc_id"], corp_code=r["corp_code"],
               path=r["path"], title=r["title"], text=body[:max_chars],
               content_class=r["content_class"], base_year=r["base_year"],
               doc_group=r["doc_group"])


def _unpack(blob: bytes, dim: int) -> list[float]:  # 테스트/디버깅용
    return list(struct.unpack(f"<{dim}f", blob))
