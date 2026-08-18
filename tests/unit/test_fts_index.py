"""FTS5 디스크 색인 테스트 (메모리 제약 대응 경로의 회귀 가드).

🔴 이 테스트가 지키는 것: 색인을 디스크로 옮기면서 **검색 계약이 깨지지 않았는지**.
인메모리 BM25와 결과가 100% 같을 필요는 없다(FTS5 bm25()는 k1/b가 고정) —
같아야 하는 것은 호출 계약·필터 정확성·토크나이저 불일치 감지다.
"""

from __future__ import annotations

import sqlite3

import pytest

from conftest import needs_index

pytestmark = [needs_index, pytest.mark.index]


@pytest.fixture(scope="module")
def fts(conn):
    from dart_agent.retrieval.fts_index import FtsIndex, build_fts, fts_ready
    from dart_agent.retrieval.tokenizer import default_tokenizer

    tok = default_tokenizer()
    if not fts_ready(conn, expect_tokenizer=tok.mode):
        build_fts(conn, tokenizer=tok, limit=400)  # 테스트용 소규모 색인
    return FtsIndex(conn, tokenizer=tok)


def test_index_is_populated(fts):
    assert fts.size > 0


def test_search_returns_hits_with_provenance(fts):
    """SearchHit는 doc 전체를 실어야 한다 — 인용 생성이 여기에 의존한다."""
    hits = fts.search("사업의 개요", top_k=5)
    if not hits:
        pytest.skip("부분 색인으로 매칭 없음")
    h = hits[0]
    assert h.doc_key and h.doc.doc_id and h.doc.path
    assert h.reasons == ["bm25"]


def test_scores_are_descending(fts):
    """bm25()는 작을수록 관련도가 높다 — 부호를 뒤집어 BM25Index 의미와 맞춰야 한다."""
    hits = fts.search("배당 정책", top_k=10)
    if len(hits) < 2:
        pytest.skip("매칭 부족")
    scores = [h.score for h in hits]
    assert scores == sorted(scores, reverse=True), scores


def test_corp_filter_pushdown(fts):
    hits = fts.search("사업의 개요", top_k=10)
    if not hits:
        pytest.skip("매칭 없음")
    corp = hits[0].doc.corp_code
    filtered = fts.search("사업의 개요", top_k=10, corp_codes={corp})
    assert filtered
    assert all(h.doc.corp_code == corp for h in filtered)


def test_year_filter_pushdown(fts):
    hits = fts.search("사업의 개요", top_k=20)
    years = {h.doc.base_year for h in hits if h.doc.base_year}
    if not years:
        pytest.skip("base_year 없음")
    y = sorted(years)[0]
    assert all(h.doc.base_year == y for h in fts.search("사업의 개요", top_k=20, years={y}))


def test_empty_query_returns_empty(fts):
    assert fts.search("") == []
    assert fts.search("   ") == []


def test_fts5_syntax_chars_do_not_crash(fts):
    """🔴 사용자 질의에 FTS5 연산자가 섞여도 500이 나면 안 된다 (AC-API2).

    `"`·`*`·`(`·`:`·`^`·`-`는 FTS5 질의 문법에서 의미를 가진다.
    """
    for q in ['삼성전자 "매출"', "매출 AND OR NOT", "투자 * 계획", "(주)삼성 - 실적",
              "NEAR(매출 영업이익)", "^배당", "col:value"]:
        hits = fts.search(q, top_k=3)
        assert isinstance(hits, list)


def test_tokenizer_mismatch_is_detected(conn):
    """토크나이저가 바뀌면 색인 토큰이 호환되지 않는다 — 재빌드를 유도해야 한다."""
    from dart_agent.retrieval.fts_index import fts_ready

    assert not fts_ready(conn, expect_tokenizer="__nonexistent__")


def test_missing_table_reports_not_ready():
    from dart_agent.retrieval.fts_index import fts_ready

    empty = sqlite3.connect(":memory:")
    empty.row_factory = sqlite3.Row
    assert not fts_ready(empty)


def test_result_overlap_with_inmemory_bm25(conn, fts):
    """인메모리 BM25와 상위 결과가 크게 어긋나지 않아야 한다 (품질 회귀 가드).

    완전 일치는 요구하지 않는다 — FTS5 bm25()는 k1=1.2/b=0.75 고정이라 미세하게 다르다.
    실측(6,504섹션·8질의): top-5 중복 92%.
    """
    from dart_agent.retrieval.bm25 import load_index

    bm = load_index(conn, limit=400)
    if bm.size == 0:
        pytest.skip("색인 없음")
    # 🔴 모집단을 맞춘다. 인메모리는 limit=400인데 FTS는 전량이면 상위 결과가
    #    겹칠 이유가 없다 — 비교가 성립하려면 같은 문서 집합을 봐야 한다.
    same_docs = {d.doc_id for d in bm.docs}
    total = hits = 0
    for q in ("배당 정책", "연구개발 조직", "사업의 개요"):
        a = {h.doc_key for h in bm.search(q, top_k=5)}
        b = {h.doc_key for h in fts.search(q, top_k=5, doc_ids=same_docs)}
        if not a:
            continue
        total += len(a)
        hits += len(a & b)
    if not total:
        pytest.skip("비교 대상 없음")
    assert hits / total >= 0.5, f"상위 결과 중복률 {hits}/{total} — 검색 품질 회귀 의심"
