#!/usr/bin/env python3
"""Retrieval A/B — BM25 단독 vs 벡터 단독 vs RRF 하이브리드 (골든셋 section 25문항).

🔴 **왜 서버 E2E가 아니라 retrieval 레벨인가**
   section 문항은 서버 경로에서 `get_section`(결정론 주소 조회)으로 답한다 —
   `doc_search`는 그 조회가 비었을 때의 폴백이다. 서버 E2E는 이미 186/186이라
   하이브리드의 효과가 **관측 자체가 안 된다**. 여기서는 같은 질문으로
   doc_search를 직접 때려 기대 섹션의 순위(hit@k/MRR)를 팔별로 잰다.

🔴 **판정 기준**: 기대 섹션 = (질문 기업의 corp_code) AND (path가 expect_section
   자체거나 그 하위). 연도는 묻지 않는다 — 질문에 연도가 없고 어느 유효본이든
   해당 주소 섹션이면 근거로 성립한다.

🔴 **silent fallback 차단**: doc_search는 질의 임베딩 실패 시 BM25로 조용히
   강등한다(운영 정책). A/B에서 그게 발동하면 하이브리드 팔이 BM25 사본이 되어
   비교가 오염된다 — 질의 벡터를 **미리 계산**해 고정 임베더로 주입한다.
   부수 효과로 질의당 임베딩 요청도 1회로 준다.

사용:
    python3 eval/retrieval_ab.py                # 25문항 전체
    python3 eval/retrieval_ab.py --out eval/retrieval_ab.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from dart_agent.envfile import load_env  # noqa: E402


class _FixedEmbedder:
    """미리 계산한 질의 벡터를 돌려주는 임베더 (네트워크 없음 = 실패 없음)."""

    name = "fixed"

    def __init__(self, vec: list[float]) -> None:
        self._vec = vec

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vec for _ in texts]


def _rank(hits, corp_codes: set[str], expect: str) -> int | None:
    for i, h in enumerate(hits, start=1):
        d = h.doc
        if d.corp_code in corp_codes and (
            d.path == expect or d.path.startswith(expect + "-")
        ):
            return i
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--goldset", type=Path, default=ROOT / "eval" / "goldset.jsonl")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--top-k", type=int, default=8)
    args = ap.parse_args()

    load_env(ROOT / ".env")
    from dart_agent.agent import tools
    from dart_agent.config import load_config
    from dart_agent.llm.provider import build_providers
    from dart_agent.retrieval.fts_index import FtsIndex, fts_ready
    from dart_agent.retrieval.tokenizer import default_tokenizer
    from dart_agent.retrieval.vectors import VectorStore
    from dart_agent.store import alias
    from dart_agent.store.db import connect

    cfg = load_config()
    _llm, emb, _ = build_providers(cfg)
    if emb is None:
        print("🔴 CLOVA 키 없음 — 질의 임베딩 불가", file=sys.stderr)
        return 2
    conn = connect(cfg.db_path, read_only=True)
    if not fts_ready(conn, expect_tokenizer=default_tokenizer().mode):
        print("🔴 FTS5 색인 없음 — scripts/build_index.py 먼저", file=sys.stderr)
        return 2
    index = FtsIndex(conn, tokenizer=default_tokenizer())
    vs = VectorStore.load(cfg.vectors_path, model=emb.name)
    if vs is None:
        print(f"🔴 벡터 스토어 없음: {cfg.vectors_path}", file=sys.stderr)
        return 2

    qs = [json.loads(ln) for ln in args.goldset.open(encoding="utf-8")]
    qs = [q for q in qs if q.get("kind") == "section"]
    print(f"section {len(qs)}문항 · 벡터 {vs.size:,}개 · top_k={args.top_k}\n")

    rows, arms = [], ("bm25", "vec", "hybrid")
    for q in qs:
        question, expect = q["question"], q["expect_section"]
        corps = alias.find_in_text(conn, question)
        if not corps:
            rows.append({"qid": q["question_id"], "error": "기업 미해석"})
            continue
        cset = set(corps)
        # 커버리지 검증 — 벡터 스토어에 해당 기업 섹션이 없으면 하이브리드 팔이
        # 조용히 BM25 사본이 된다 (Codex 리뷰). 그 문항은 오류로 명시한다.
        n_cov = sum(1 for m in vs.meta if m[0] in cset)
        if n_cov == 0:
            rows.append({"qid": q["question_id"], "error": "벡터 스토어 미커버 기업"})
            continue

        # 질의 벡터 1회 계산 (429는 재시도 — 실패는 크게 알린다)
        qvec = None
        for attempt in range(4):
            try:
                qvec = emb.embed([question])[0]
                break
            except Exception as exc:
                print(f"  ⚠️ 질의 임베딩 실패({str(exc)[:60]}) → 재시도", flush=True)
                time.sleep(5 * 2 ** attempt)
        if qvec is None:
            rows.append({"qid": q["question_id"], "error": "질의 임베딩 실패"})
            continue
        fixed = _FixedEmbedder(qvec)

        r_bm = _rank(tools.doc_search(index, question, corp=corps, top_k=args.top_k),
                     cset, expect)
        r_vec = _rank(vs.search(qvec, top_k=args.top_k, corp_codes=cset), cset, expect)
        r_hy = _rank(tools.doc_search(index, question, corp=corps, top_k=args.top_k,
                                      vectors=vs, embedder=fixed, conn=conn,
                                      rrf_k=cfg.hybrid_rrf_k,
                                      vec_weight=cfg.hybrid_vec_weight),
                     cset, expect)
        rows.append({"qid": q["question_id"], "corp": q["meta"]["corp"],
                     "expect": expect, "bm25": r_bm, "vec": r_vec, "hybrid": r_hy})
        fmt = lambda r: str(r) if r else "—"
        print(f"  {q['question_id']:<8} {q['meta']['corp']:<10} {expect:<6} "
              f"bm25={fmt(r_bm):<3} vec={fmt(r_vec):<3} hybrid={fmt(r_hy)}")

    ok = [r for r in rows if "error" not in r]
    errs = [r for r in rows if "error" in r]
    print(f"\n{'=' * 62}")
    if errs:
        print(f"  🔴 평가 불능 {len(errs)}건 (분모 제외 — 지표 해석 주의):")
        for e in errs:
            print(f"     {e['qid']}: {e['error']}")
    summary = {}
    for arm in arms:
        ranks = [r[arm] for r in ok]
        n = len(ranks)
        s = {
            "hit@1": sum(1 for r in ranks if r and r <= 1),
            "hit@3": sum(1 for r in ranks if r and r <= 3),
            f"hit@{args.top_k}": sum(1 for r in ranks if r),
            "MRR": round(sum(1 / r for r in ranks if r) / n, 3) if n else 0.0,
        }
        summary[arm] = s
        print(f"  {arm:<7} hit@1 {s['hit@1']}/{n} · hit@3 {s['hit@3']}/{n} · "
              f"hit@{args.top_k} {s[f'hit@{args.top_k}']}/{n} · MRR {s['MRR']}")
    print(f"{'=' * 62}")

    if args.out:
        args.out.write_text(json.dumps(
            {"top_k": args.top_k, "vector_count": vs.size,
             "errors": len(errs), "summary": summary, "items": rows},
            ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"저장 → {args.out}")
    return 1 if errs else 0   # 평가 불능 문항이 있으면 성공으로 위장하지 않는다


if __name__ == "__main__":
    raise SystemExit(main())
