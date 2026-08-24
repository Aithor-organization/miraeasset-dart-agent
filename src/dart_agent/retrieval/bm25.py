"""BM25 + RRF 융합 (SPEC §3 AC-R3~R5).

RRF 수식은 AITHOR `rag.py:_rrf_fuse`와 동일 (k=60, Σ 1/(k+rank)) — 검증된 구현 차용.
벡터 랭킹은 EmbeddingProvider가 None이면 건너뛰고 BM25 단독으로 동작한다 (AC-R5,
키 없는 환경 대응).
"""

from __future__ import annotations

import math
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .tokenizer import Tokenizer, default_tokenizer


@dataclass
class Doc:
    """색인 단위 = 섹션 1개 (parent-document retrieval의 parent)."""

    doc_key: str  # section_id
    doc_id: str
    corp_code: str
    path: str
    title: str
    text: str
    content_class: str
    base_year: int | None = None
    doc_group: str = ""


@dataclass
class SearchHit:
    doc_key: str
    score: float
    doc: Doc
    reasons: list[str] = field(default_factory=list)


class BM25Index:
    """메모리 상주 BM25. 섹션 수 수만 규모까지 실용적."""

    def __init__(self, k1: float = 1.2, b: float = 0.75, tokenizer: Tokenizer | None = None):
        self.k1, self.b = k1, b
        self.tok = tokenizer or default_tokenizer()
        self.docs: list[Doc] = []
        self._tf: list[Counter[str]] = []
        self._len: list[int] = []
        self._df: Counter[str] = Counter()
        self._postings: dict[str, list[int]] = defaultdict(list)
        self._avg = 0.0

    def add(self, doc: Doc, index_text: str | None = None) -> None:
        """index_text를 주면 그것을 색인한다 (레지스트리 표는 라벨만 색인, AC-R3)."""
        body = index_text if index_text is not None else doc.text
        toks = self.tok.tokens(f"{doc.title} {body}")
        tf = Counter(toks)
        i = len(self.docs)
        self.docs.append(doc)
        self._tf.append(tf)
        self._len.append(max(len(toks), 1))
        for term in tf:
            self._df[term] += 1
            self._postings[term].append(i)

    def finalize(self) -> None:
        self._avg = (sum(self._len) / len(self._len)) if self._len else 1.0

    @property
    def size(self) -> int:
        return len(self.docs)

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
        """필터 우선 적용 후 BM25 스코어링 (정확도·비용 동시 개선)."""
        qt = self.tok.tokens(query)
        if not qt or not self.docs:
            return []
        if self._avg <= 0:
            self.finalize()

        cand: set[int] | None = None
        for term in set(qt):
            for i in self._postings.get(term, ()):
                if cand is None:
                    cand = set()
                cand.add(i)
        if not cand:
            return []

        N = len(self.docs)
        scores: dict[int, float] = {}
        for i in cand:
            d = self.docs[i]
            if corp_codes and d.corp_code not in corp_codes:
                continue
            if doc_groups and d.doc_group not in doc_groups:
                continue
            if years and (d.base_year not in years):
                continue
            if doc_ids and d.doc_id not in doc_ids:
                continue
            tf, dl = self._tf[i], self._len[i]
            s = 0.0
            for term in qt:
                f = tf.get(term, 0)
                if not f:
                    continue
                df = self._df[term]
                idf = math.log(1 + (N - df + 0.5) / (df + 0.5))
                s += idf * (f * (self.k1 + 1)) / (
                    f + self.k1 * (1 - self.b + self.b * dl / self._avg)
                )
            if s > 0:
                scores[i] = s
        ranked = sorted(scores.items(), key=lambda kv: -kv[1])[:top_k]
        return [
            SearchHit(self.docs[i].doc_key, s, self.docs[i], ["bm25"]) for i, s in ranked
        ]


def rrf_fuse(rankings: list[list[SearchHit]], *, k: int = 60,
             weights: list[float] | None = None) -> list[SearchHit]:
    """Reciprocal Rank Fusion — AITHOR `_rrf_fuse`와 동일 수식 (AC-R4).

    🔴 **k와 가중치는 도메인에 맞춰 실측할 것.** k=60은 원논문 기본값이지만
    본 코퍼스에서는 나빴다 — 골든셋 section 25문항 실측 (2026-08-24):

        BM25 단독        MRR 0.290
        벡터 단독        MRR 0.502
        RRF k=60 1:1    MRR 0.419   ← 표준값. 벡터 단독보다도 나쁘다
        RRF k=10 1:2    MRR 0.620   ← 채택

    k가 크면 상위 랭크의 우위가 평탄해진다. 두 팔의 품질이 비슷할 때는 그게
    안정적이지만, 여기처럼 한쪽(벡터)이 확연히 우수하면 **좋은 팔을 나쁜 팔이
    끌어내린다**. k를 줄이고 우수한 팔에 가중치를 주면 그 희석이 사라진다.
    ⚠️ 25문항 튜닝이라 과적합 여지가 있다 — 전량 임베딩 후 재측정할 것.
    """
    fused: dict[str, float] = defaultdict(float)
    keep: dict[str, SearchHit] = {}
    reasons: dict[str, set[str]] = defaultdict(set)
    for i, ranking in enumerate(rankings):
        w = weights[i] if weights and i < len(weights) else 1.0
        for rank, hit in enumerate(ranking, start=1):
            fused[hit.doc_key] += w / (k + rank)
            keep.setdefault(hit.doc_key, hit)
            reasons[hit.doc_key].update(hit.reasons)
    out: list[SearchHit] = []
    for key, score in sorted(fused.items(), key=lambda kv: -kv[1]):
        h = keep[key]
        out.append(SearchHit(key, score, h.doc, sorted(reasons[key])))
    return out


INDEX_FORMAT = 2  # 스키마 변경 시 올린다 — 구버전 캐시는 자동 무시된다


def save_index(idx: BM25Index, path) -> None:
    """토크나이즈 결과를 영속화한다 (기동 시간 = 다운타임이므로 필수).

    실측: 6,504 섹션 재토크나이즈에 63초. 정기공시 전량(1,054건)이면 섹션 ~11만 개로
    기동에 ~18분이 걸려 평가기간 재시작이 장시간 다운타임이 된다 (SPEC §7-3).
    """
    import pickle
    from pathlib import Path

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "format": INDEX_FORMAT,
        "k1": idx.k1, "b": idx.b, "avg": idx._avg,
        "tokenizer_mode": idx.tok.mode,
        "docs": idx.docs, "tf": idx._tf, "len": idx._len,
        "df": idx._df, "postings": dict(idx._postings),
    }
    tmp = p.with_suffix(p.suffix + ".tmp")
    with tmp.open("wb") as f:
        pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)
    tmp.replace(p)  # atomic


def load_saved_index(path, *, expect_tokenizer: str | None = None) -> BM25Index | None:
    """영속 인덱스 로드. 없거나 포맷/토크나이저가 다르면 None → 재빌드."""
    import pickle
    from collections import defaultdict
    from pathlib import Path

    p = Path(path)
    if not p.exists():
        return None
    try:
        with p.open("rb") as f:
            d = pickle.load(f)
    except Exception:
        return None
    if d.get("format") != INDEX_FORMAT:
        return None
    if expect_tokenizer and d.get("tokenizer_mode") != expect_tokenizer:
        # 토크나이저가 바뀌면(kiwi↔ngram) 색인 토큰이 호환되지 않는다
        return None
    idx = BM25Index(k1=d["k1"], b=d["b"])
    idx.docs = d["docs"]
    idx._tf = d["tf"]
    idx._len = d["len"]
    idx._df = d["df"]
    idx._postings = defaultdict(list, d["postings"])
    idx._avg = d["avg"]
    return idx


def load_index(
    conn: sqlite3.Connection, *, k1: float = 1.2, b: float = 0.75, limit: int = 0
) -> BM25Index:
    """section 테이블 → BM25 인덱스. 선별 색인 정책 적용 (AC-R3).

    prose         → 제목 + 본문 색인
    financial_stmt→ 제목 + 표 markdown (수치 조회는 Fact Store가 담당하나 검색 가능성 유지)
    table_registry→ 제목만 (볼륨 47%를 차지하는 인명·계열사 표 본문 제외)
    """
    idx = BM25Index(k1=k1, b=b)
    sql = (
        "SELECT s.section_id,s.doc_id,s.corp_code,s.path,s.title,s.text,s.tables_md,"
        "       s.content_class,d.base_year,d.doc_group "
        "FROM section s JOIN document d ON d.doc_id=s.doc_id "
        "WHERE d.is_effective=1"
    )
    if limit:
        sql += f" LIMIT {int(limit)}"
    for r in conn.execute(sql):
        doc = Doc(
            doc_key=r["section_id"], doc_id=r["doc_id"], corp_code=r["corp_code"],
            path=r["path"], title=r["title"], text=r["text"] or "",
            content_class=r["content_class"], base_year=r["base_year"],
            doc_group=r["doc_group"],
        )
        cls = r["content_class"]
        if cls == "table_registry":
            body = ""
        elif cls == "financial_stmt":
            body = (r["tables_md"] or "")[:20_000]
        else:
            body = (r["text"] or "")[:60_000]
        idx.add(doc, index_text=body)
    idx.finalize()
    return idx
