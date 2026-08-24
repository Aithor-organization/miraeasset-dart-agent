"""벡터 스토어 + 하이브리드 doc_search 단위 테스트 (네트워크 없음).

검증 대상:
- VectorStore 저장/로드/코사인 순서/기업 필터 (numpy 경로 + 순수 파이썬 폴백)
- doc_search: vectors 미주입 시 기존 BM25 동작 그대로 (회귀 가드)
- doc_search 하이브리드: RRF 융합 순서 + BM25 점수 의미 보존 + 임베딩 실패 강등
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from dart_agent.agent import tools  # noqa: E402
from dart_agent.retrieval import vectors as V  # noqa: E402
from dart_agent.retrieval.bm25 import BM25Index, Doc  # noqa: E402


def _mk_store(tmp_path, rows):
    conn = V.open_store(tmp_path / "emb.sqlite")
    V.save_vectors(conn, "test-model", rows)
    conn.close()
    return V.VectorStore.load(tmp_path / "emb.sqlite")


def _row(sid, corp, vec):
    return (sid, corp, f"doc-{corp}", "II-1", f"제목 {sid}", 2025, vec)


class TestVectorStore:
    def test_roundtrip_and_cosine_order(self, tmp_path):
        vs = _mk_store(tmp_path, [
            _row("s1", "C1", [1.0, 0.0, 0.0]),
            _row("s2", "C1", [0.7, 0.7, 0.0]),
            _row("s3", "C2", [0.0, 1.0, 0.0]),
        ])
        assert vs.size == 3 and vs.dim == 3
        hits = vs.search([1.0, 0.1, 0.0], top_k=3)
        assert [h.doc_key for h in hits] == ["s1", "s2", "s3"]
        assert hits[0].score > hits[1].score > hits[2].score
        assert hits[0].reasons == ["vec"]

    def test_corp_filter(self, tmp_path):
        vs = _mk_store(tmp_path, [
            _row("s1", "C1", [1.0, 0.0, 0.0]),
            _row("s3", "C2", [0.9, 0.1, 0.0]),
        ])
        hits = vs.search([1.0, 0.0, 0.0], top_k=5, corp_codes={"C2"})
        assert [h.doc_key for h in hits] == ["s3"]

    def test_pure_python_fallback_matches_numpy(self, tmp_path, monkeypatch):
        rows = [
            _row("s1", "C1", [0.9, 0.1, 0.2]),
            _row("s2", "C1", [0.1, 0.9, 0.3]),
            _row("s3", "C1", [0.4, 0.4, 0.8]),
        ]
        q = [0.5, 0.5, 0.1]
        vs_np = _mk_store(tmp_path, rows)
        np_keys = [h.doc_key for h in vs_np.search(q, top_k=3)]  # 패치 전에 계산
        monkeypatch.setattr(V, "_np", None)
        vs_py = V.VectorStore.load(tmp_path / "emb.sqlite")
        assert vs_py._mat is None  # 폴백 경로 확인
        py_keys = [h.doc_key for h in vs_py.search(q, top_k=3)]
        assert np_keys == py_keys

    def test_load_missing_returns_none(self, tmp_path):
        assert V.VectorStore.load(tmp_path / "none.sqlite") is None

    def test_dim_mismatch_query(self, tmp_path):
        vs = _mk_store(tmp_path, [_row("s1", "C1", [1.0, 0.0, 0.0])])
        assert vs.search([1.0, 0.0], top_k=3) == []


def _bm25_with(docs):
    idx = BM25Index()
    for key, corp, title, text in docs:
        idx.add(Doc(doc_key=key, doc_id=f"d-{key}", corp_code=corp, path="II-1",
                    title=title, text=text, content_class="prose",
                    base_year=2025, doc_group="periodic"))
    idx.finalize()
    return idx


class _FakeEmbedder:
    name = "fake"

    def __init__(self, vec, fail=False):
        self.vec, self.fail = vec, fail

    def embed(self, texts):
        if self.fail:
            raise RuntimeError("simulated 429")
        return [self.vec for _ in texts]


class TestHybridDocSearch:
    def test_without_vectors_unchanged(self):
        idx = _bm25_with([("a", "C1", "매출 개요", "매출 증가"),
                          ("b", "C1", "배당", "배당 정책")])
        hits = tools.doc_search(idx, "매출", corp=["C1"], top_k=5)
        assert hits and hits[0].doc_key == "a"
        assert hits[0].reasons == ["bm25"]

    def test_fusion_promotes_vector_hit(self, tmp_path):
        # BM25는 a만 찾는 질의에서, 벡터가 b를 밀어 올리는지
        idx = _bm25_with([("a", "C1", "매출 개요", "매출 증가 매출"),
                          ("b", "C1", "사업 현황", "성장 전략")])
        vs = _mk_store(tmp_path, [_row("b", "C1", [1.0, 0.0, 0.0])])
        emb = _FakeEmbedder([1.0, 0.0, 0.0])
        hits = tools.doc_search(idx, "매출", corp=["C1"], top_k=5,
                                vectors=vs, embedder=emb)
        keys = [h.doc_key for h in hits]
        assert "a" in keys and "b" in keys
        by_key = {h.doc_key: h for h in hits}
        assert "vec" in by_key["b"].reasons
        # 점수 의미: a는 BM25 점수 유지, 벡터 단독 b는 코사인 유사도(0~1)
        assert by_key["a"].score > 0.0
        assert 0.0 < by_key["b"].score <= 1.0 + 1e-6

    def test_embed_failure_degrades_to_bm25(self, tmp_path):
        idx = _bm25_with([("a", "C1", "매출 개요", "매출 증가")])
        vs = _mk_store(tmp_path, [_row("b", "C1", [1.0, 0.0, 0.0])])
        emb = _FakeEmbedder([1.0, 0.0, 0.0], fail=True)
        hits = tools.doc_search(idx, "매출", corp=["C1"], top_k=5,
                                vectors=vs, embedder=emb)
        assert [h.doc_key for h in hits] == ["a"]  # 벡터 팔 없이 성립

    def test_years_filter_applies_to_vector_arm(self, tmp_path):
        # 2023년 질의에 2025년 벡터 히트가 끼어들면 안 된다
        idx = _bm25_with([("a", "C1", "매출 개요", "매출 증가")])
        vs = _mk_store(tmp_path, [_row("v25", "C1", [1.0, 0.0, 0.0])])  # base_year=2025
        emb = _FakeEmbedder([1.0, 0.0, 0.0])
        hits = tools.doc_search(idx, "매출", corp=["C1"], years=[2023], top_k=5,
                                vectors=vs, embedder=emb)
        assert "v25" not in [h.doc_key for h in hits]

    def test_corp_filter_applies_to_vector_arm(self, tmp_path):
        idx = _bm25_with([("a", "C1", "매출 개요", "매출 증가")])
        vs = _mk_store(tmp_path, [_row("x", "C9", [1.0, 0.0, 0.0])])
        emb = _FakeEmbedder([1.0, 0.0, 0.0])
        hits = tools.doc_search(idx, "매출", corp=["C1"], top_k=5,
                                vectors=vs, embedder=emb)
        assert all(h.doc.corp_code == "C1" for h in hits)

    def test_doc_groups_gate_skips_vector_arm(self, tmp_path):
        # 벡터 스토어는 periodic 전용 — 다른 그룹 질의에 벡터 문서 유입 금지
        idx = _bm25_with([("a", "C1", "지분 변동", "지분 공시")])
        vs = _mk_store(tmp_path, [_row("v", "C1", [1.0, 0.0, 0.0])])
        emb = _FakeEmbedder([1.0, 0.0, 0.0])
        hits = tools.doc_search(idx, "지분", corp=["C1"], doc_groups=["holding"],
                                top_k=5, vectors=vs, embedder=emb)
        assert "v" not in [h.doc_key for h in hits]

    def test_bm25_top1_always_survives_fusion(self, tmp_path):
        # BM25 1위가 RRF 절단에서 밀려도 결과에 남아 max(score)가 보존돼야 한다
        docs = [("bm_top", "C1", "매출 개요 상세", "매출 " * 30)]
        docs += [(f"d{i}", "C1", f"기타 {i} 매출", "매출 언급") for i in range(9)]
        idx = _bm25_with(docs)
        # 벡터는 d0~d8만 강하게 밀어 올린다 (bm_top은 벡터 스토어에 없음)
        vs = _mk_store(tmp_path, [
            _row(f"d{i}", "C1", [1.0, 0.001 * i, 0.0]) for i in range(9)
        ])
        emb = _FakeEmbedder([1.0, 0.0, 0.0])
        hits = tools.doc_search(idx, "매출 개요 상세", corp=["C1"], top_k=3,
                                vectors=vs, embedder=emb)
        bm_top = idx.search("매출 개요 상세", top_k=1)[0]
        assert bm_top.doc_key in [h.doc_key for h in hits]
        assert max(h.score for h in hits) >= bm_top.score

    def test_vector_only_scenario_gets_cosine_score(self, tmp_path):
        # BM25가 0건인 질의에서 벡터 히트가 0점으로 죽지 않는다 (기권 게이트 통과 가능)
        idx = _bm25_with([("a", "C1", "매출 개요", "매출 증가")])
        vs = _mk_store(tmp_path, [_row("v", "C1", [1.0, 0.0, 0.0])])
        emb = _FakeEmbedder([1.0, 0.0, 0.0])
        hits = tools.doc_search(idx, "존재하지않는어휘질의", corp=["C1"], top_k=5,
                                vectors=vs, embedder=emb)
        if hits:  # BM25 0건 → 벡터 단독 결과
            assert max(h.score for h in hits) > 0.35


class TestEmbedTextPolicy:
    def test_registry_label_only(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
        from embed_sections import embed_text
        row = {"content_class": "table_registry", "corp_name": "삼성", "path": "IX",
               "title": "계열사 현황", "text": "긴 표 본문" * 100, "tables_md": "|a|b|"}
        assert embed_text(row, 600) == "삼성 IX 계열사 현황"

    def test_prose_truncated(self):
        from embed_sections import embed_text
        row = {"content_class": "prose", "corp_name": "삼성", "path": "II-1",
               "title": "개요", "text": "x" * 5000, "tables_md": ""}
        out = embed_text(row, 600)
        assert len(out) <= len("삼성 II-1 개요\n") + 600
