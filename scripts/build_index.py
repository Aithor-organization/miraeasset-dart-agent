#!/usr/bin/env python3
"""인덱스 빌더 — raw/ 5.3GB → SQLite Fact Store (SPEC §2 AC-S4, AC-C4).

재실행 안전(idempotent). 실패 문서를 침묵하지 않고 리포트한다 (AC-P1 정신).

사용:
  python3 scripts/build_index.py --limit 50                 # 빠른 스모크
  python3 scripts/build_index.py --groups exchange,holding  # 특정 유형만
  python3 scripts/build_index.py                            # 전체 4,204건
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dart_agent.config import CONFIG  # noqa: E402
from dart_agent.models import DocMeta  # noqa: E402
from dart_agent.parsers.exchange import ExchangeParser  # noqa: E402
from dart_agent.parsers.holding import HoldingParser  # noqa: E402
from dart_agent.parsers.major import MajorParser  # noqa: E402
from dart_agent.parsers.periodic import PeriodicParser  # noqa: E402
from dart_agent.store import repository  # noqa: E402
from dart_agent.store.alias import build_alias_table, load_universe  # noqa: E402
from dart_agent.store.corrections import resolve_chains  # noqa: E402
from dart_agent.retrieval.bm25 import load_index, save_index  # noqa: E402
from dart_agent.retrieval.fts_index import build_fts  # noqa: E402
from dart_agent.retrieval.tokenizer import default_tokenizer  # noqa: E402
from dart_agent.store.db import connect, init_schema, set_meta, table_counts  # noqa: E402

PARSERS = {
    "periodic": PeriodicParser(),
    "major": MajorParser(),
    "exchange": ExchangeParser(),
    "holding": HoldingParser(),
}


def to_meta(row: dict) -> DocMeta:
    return DocMeta(
        doc_id=row["doc_id"],
        corp_code=str(row["corp_code"]),
        corp_name=row["corp_name"],
        doc_group=row["doc_group"],
        doc_subtype=row.get("doc_subtype"),
        report_nm=row["report_nm"],
        rcept_no=str(row["rcept_no"]),
        rcept_dt=str(row["rcept_dt"]),
        is_correction=bool(row.get("is_correction")),
        base_year=row.get("base_year"),
        base_month=row.get("base_month"),
        file_path=row["file_path"],
        file_format=row.get("file_format", "xml"),
        listed_name=row.get("listed_name"),
        stock_code=str(row["stock_code"]) if row.get("stock_code") else None,
        industry=row.get("industry"),
        sector=row.get("sector"),
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", type=Path, default=CONFIG.corpus_root)
    ap.add_argument("--db", type=Path, default=CONFIG.db_path)
    ap.add_argument("--limit", type=int, default=0, help="유형별 최대 문서수 (0=전체)")
    ap.add_argument("--groups", type=str, default="periodic,major,exchange,holding")
    ap.add_argument("--rebuild", action="store_true", help="기존 DB 삭제 후 재빌드")
    ap.add_argument("--progress-every", type=int, default=100)
    ap.add_argument("--also-pickle", action="store_true",
                    help="레거시 인메모리 BM25 캐시도 함께 생성 (회귀 비교용, 메모리 큼)")
    args = ap.parse_args()

    corpus: Path = args.corpus
    manifest = corpus / "manifest.jsonl"
    if not manifest.exists():
        print(f"❌ manifest 없음: {manifest}", file=sys.stderr)
        return 2

    if args.rebuild and args.db.exists():
        args.db.unlink()
        for suffix in ("-wal", "-shm"):
            p = args.db.with_name(args.db.name + suffix)
            if p.exists():
                p.unlink()

    groups = [g.strip() for g in args.groups.split(",") if g.strip()]
    rows = [json.loads(line) for line in manifest.open(encoding="utf-8")]
    rows = [r for r in rows if r["doc_group"] in groups]
    if args.limit:
        per: Counter[str] = Counter()
        kept = []
        for r in rows:
            if per[r["doc_group"]] < args.limit:
                kept.append(r)
                per[r["doc_group"]] += 1
        rows = kept

    conn = connect(args.db)
    init_schema(conn)

    # 1) 기업 마스터 + 별칭
    universe = load_universe(corpus / "universe.csv")
    alias_stats = build_alias_table(conn, universe)
    print(f"✅ 기업 {alias_stats['companies']}개 · 별칭 "
          f"{alias_stats['aliases'] + alias_stats['manual']}건 "
          f"(수기 {alias_stats['manual']}, 미연결 {alias_stats['skipped_manual']})")

    # 2) 문서 파싱 + 적재
    t0 = time.time()
    totals: Counter[str] = Counter()
    warn_docs: list[tuple[str, str]] = []
    failed: list[tuple[str, str]] = []
    by_group: Counter[str] = Counter()

    for i, row in enumerate(rows, 1):
        meta = to_meta(row)
        parser = PARSERS.get(meta.doc_group)
        if parser is None:
            failed.append((meta.doc_id, "no parser"))
            continue
        try:
            res = parser.parse(meta, corpus)
        except Exception as exc:  # 파서는 던지지 않아야 하지만 최후 방어
            failed.append((meta.doc_id, f"{type(exc).__name__}: {exc}"))
            continue
        try:
            repository.clear_doc(conn, meta.doc_id)
            counts = repository.store(conn, res)
            conn.commit()
        except Exception as exc:
            conn.rollback()
            failed.append((meta.doc_id, f"store {type(exc).__name__}: {exc}"))
            continue
        for k, v in counts.items():
            totals[k] += v
        by_group[meta.doc_group] += 1
        if res.warnings:
            warn_docs.append((meta.doc_id, res.warnings[0][:90]))
        if args.progress_every and i % args.progress_every == 0:
            el = time.time() - t0
            print(f"   … {i}/{len(rows)} ({el:.0f}s, {i / max(el, 0.01):.1f} doc/s)", flush=True)

    elapsed = time.time() - t0
    print(f"✅ 문서 {sum(by_group.values())}/{len(rows)} 적재 ({elapsed:.0f}s) · {dict(by_group)}")
    print(f"   레코드: {dict(totals)}")

    # 3) 정정 체인 해소 (AC-C4 — 매칭률 보고 의무)
    chain = resolve_chains(conn)
    print(f"✅ {chain.summary()}")

    # 4) 빌드 메타
    set_meta(conn, "built_at_epoch", str(int(time.time())))
    set_meta(conn, "corpus_root", str(corpus))
    set_meta(conn, "doc_count", str(sum(by_group.values())))
    set_meta(conn, "groups", ",".join(groups))
    set_meta(conn, "limit", str(args.limit))
    set_meta(conn, "correction_match_rate", f"{chain.match_rate:.4f}")
    conn.commit()

    counts = table_counts(conn)
    print(f"✅ 테이블: {counts}")

    # 5) FTS5 색인 — 기동 시간 = 평가기간 다운타임이므로 영속화 필수 (SPEC §7-3).
    #    🔴 인메모리 BM25(pickle)는 전체 코퍼스에서 ~9.9 GB로 추정되어 4 GB 서버에
    #    올라가지 않는다. FTS5는 DB 안에 두고 조회 시에만 읽으므로 상주 메모리가 무관하다.
    t1 = time.time()
    tok = default_tokenizer()
    n_sec = build_fts(conn, tokenizer=tok)
    db_mb = args.db.stat().st_size / 1e6  # 🔴 CONFIG가 아니라 args.db — --db 오버라이드 존중
    print(f"✅ FTS5 색인 {n_sec:,} 섹션 · 토크나이저={tok.mode} · "
          f"빌드 {time.time() - t1:.0f}s → {args.db} (DB 전체 {db_mb:.0f}MB)")
    set_meta(conn, "bm25_sections", str(n_sec))
    set_meta(conn, "bm25_tokenizer", tok.mode)
    conn.commit()

    if args.also_pickle:  # 레거시 인메모리 캐시 (비교·회귀 검증용)
        t2 = time.time()
        idx = load_index(conn, k1=CONFIG.bm25_k1, b=CONFIG.bm25_b)
        save_index(idx, CONFIG.bm25_path)
        print(f"✅ (레거시) BM25 pickle {idx.size:,} 섹션 · {time.time() - t2:.0f}s → "
              f"{CONFIG.bm25_path} ({CONFIG.bm25_path.stat().st_size / 1e6:.0f}MB)")

    # 5) 실패·경고 리포트 — 🔴 침묵 금지
    if failed:
        print(f"\n❌ 파싱/적재 실패 {len(failed)}건:")
        for doc_id, why in failed[:20]:
            print(f"   {doc_id}: {why}")
        if len(failed) > 20:
            print(f"   … 외 {len(failed) - 20}건")
    if warn_docs:
        print(f"\n⚠️  경고 있는 문서 {len(warn_docs)}건 (상위 10):")
        for doc_id, w in warn_docs[:10]:
            print(f"   {doc_id}: {w}")
    if chain.unresolved_docs:
        print(f"\n⚠️  정정 원본 미해소 {chain.unresolved}건 (상위 10): "
              f"{chain.unresolved_docs[:10]}")

    success_rate = sum(by_group.values()) / max(len(rows), 1)
    print(f"\n{'✅' if success_rate >= 0.98 else '⚠️ '} 파싱 성공률 {success_rate * 100:.1f}% "
          f"(게이트 98%)")
    conn.close()
    return 0 if success_rate >= 0.98 else 1


if __name__ == "__main__":
    raise SystemExit(main())
