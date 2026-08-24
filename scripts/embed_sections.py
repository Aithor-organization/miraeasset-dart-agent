#!/usr/bin/env python3
"""파일럿 임베딩 생성 — 골든셋 20개 기업의 최신 사업보고서 전 섹션.

🔴 **왜 전량(11만)이 아니라 파일럿인가**
   전량은 요청 수 기준 레이트리밋(60 req/min 실측)에서 **31시간+**이 든다.
   하이브리드가 BM25 대비 실제로 나은지 **증거 없이** 그 비용을 지불할 이유가
   없다. 파일럿(2,918 섹션 ≈ 53분)으로 골든셋 section 25문항 A/B를 먼저 잰다 —
   eval/retrieval_ab.py가 판정한다.

🔴 **레이트리밋 (실측 2026-08-24, 응답 헤더)**
   x-ratelimit-limit-requests: 60/min · x-ratelimit-limit-tokens: 40,000/min
   배치 불가(input=str만 허용 — clova.py 참조)라 **요청 수가 지배 제약**이다.
   600자 절단 기준 토큰은 분당 ~13K로 여유. 기본 55 req/min으로 자기 조절하고
   429는 백오프로 흡수한다. 중단돼도 재실행하면 이어서 한다 (resume).

사용:
    python3 scripts/embed_sections.py --dry-run     # 대상/예산만 출력
    python3 scripts/embed_sections.py               # 실행 (resume 지원)
    python3 scripts/embed_sections.py --limit 8     # 스모크
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

# 골든셋 section 문항이 겨냥하는 기업들 — eval/goldset.jsonl에서 추출
GOLDSET_CORPS = [
    "JYP Ent", "LG이노텍", "NC", "SK하이닉스", "고려아연", "두산에너빌리티",
    "두산퓨얼셀", "메리츠금융지주", "삼성E&A", "삼성SDI", "삼성생명", "알테오젠",
    "에스엠", "이마트", "하나금융지주", "하이브", "한화에어로스페이스",
    "현대로템", "현대오토에버", "효성중공업",
]

# scope=goldset-corps — 파일럿(20개 기업 최신 사업보고서 ≈ 2,918섹션, 52분 실측)
_SELECT_GOLDSET = """
WITH corps AS (
  SELECT corp_code FROM company
  WHERE listed_name IN ({ph}) OR corp_name IN ({ph})
),
latest AS (
  SELECT d.corp_code, d.doc_id,
         ROW_NUMBER() OVER (PARTITION BY d.corp_code
                            ORDER BY d.base_year DESC, d.rcept_dt DESC) rn
  FROM document d JOIN corps USING(corp_code)
  WHERE d.is_effective=1 AND d.doc_subtype='annual'
)
SELECT s.section_id, s.corp_code, s.doc_id, s.path, s.title, s.text, s.tables_md,
       s.content_class, d.base_year, c.corp_name
FROM section s
JOIN latest l ON l.doc_id = s.doc_id AND l.rn = 1
JOIN document d ON d.doc_id = s.doc_id
JOIN company c ON c.corp_code = s.corp_code
ORDER BY s.corp_code, s.path
"""

# scope=all-annual — 코퍼스 70개 기업 전체의 최신 사업보고서 (≈10,123섹션, ~3시간)
_SELECT_ALL_ANNUAL = """
WITH latest AS (
  SELECT d.corp_code, d.doc_id,
         ROW_NUMBER() OVER (PARTITION BY d.corp_code
                            ORDER BY d.base_year DESC, d.rcept_dt DESC) rn
  FROM document d WHERE d.is_effective=1 AND d.doc_subtype='annual'
)
SELECT s.section_id, s.corp_code, s.doc_id, s.path, s.title, s.text, s.tables_md,
       s.content_class, d.base_year, c.corp_name
FROM section s
JOIN latest l ON l.doc_id = s.doc_id AND l.rn = 1
JOIN document d ON d.doc_id = s.doc_id
JOIN company c ON c.corp_code = s.corp_code
ORDER BY s.corp_code, s.path
"""

# scope=all — 전 유효 문서 (≈112,797섹션, ~34시간). registry 표는 라벨만이라 제외해도
# 검색 품질 손실이 없다(FTS 색인 정책과 동일) → 101,779섹션 ≈ 31시간
_SELECT_ALL = """
SELECT s.section_id, s.corp_code, s.doc_id, s.path, s.title, s.text, s.tables_md,
       s.content_class, d.base_year, c.corp_name
FROM section s
JOIN document d ON d.doc_id = s.doc_id
JOIN company c ON c.corp_code = s.corp_code
WHERE d.is_effective=1 AND s.content_class != 'table_registry'
ORDER BY d.base_year DESC, s.corp_code, s.path
"""


def embed_text(row, max_chars: int) -> str:
    """색인 정책은 FTS와 동일 철학: registry는 라벨만, 표는 markdown."""
    cls = row["content_class"]
    if cls == "table_registry":
        body = ""
    elif cls == "financial_stmt":
        body = (row["tables_md"] or "")[:max_chars]
    else:
        body = (row["text"] or "")[:max_chars]
    return f"{row['corp_name']} {row['path']} {row['title']}\n{body}".strip()


class Pacer:
    """분당 요청 예산 자기 조절 (60초 슬라이딩 창)."""

    def __init__(self, per_min: int) -> None:
        self.per_min = max(1, min(per_min, 60))  # 실측 한도 60 초과·0 이하 방어
        self._stamps: list[float] = []

    def wait(self) -> None:
        now = time.time()
        self._stamps = [t for t in self._stamps if now - t < 60]
        if len(self._stamps) >= self.per_min:
            time.sleep(max(0.2, 60.0 - (now - self._stamps[0]) + 0.05))
        self._stamps.append(time.time())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=ROOT / "index" / "embeddings.sqlite")
    ap.add_argument("--max-chars", type=int, default=600)
    ap.add_argument("--req-per-min", type=int, default=55,
                    help="실측 한도 60/min에서 안전 마진 (chat과 별도 풀)")
    ap.add_argument("--save-every", type=int, default=32)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--scope", choices=("goldset-corps", "all-annual", "all"),
                    default="goldset-corps",
                    help="임베딩 대상 범위 (기본: 파일럿 20개 기업)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    load_env(ROOT / ".env")
    from dart_agent.config import load_config
    from dart_agent.llm.provider import build_providers
    from dart_agent.retrieval import vectors as V
    from dart_agent.store.db import connect

    cfg = load_config()
    _llm, emb, _notes = build_providers(cfg)
    model_name = getattr(emb, "name", None) or f"clova-{cfg.embedding_model}"
    if emb is None and not args.dry_run:   # dry-run은 키 없이도 대상 집계만 수행
        print("🔴 CLOVA 키가 없어 임베딩 불가 (.env 확인)", file=sys.stderr)
        return 2

    conn = connect(cfg.db_path, read_only=True)
    if args.scope == "goldset-corps":
        ph = ",".join("?" * len(GOLDSET_CORPS))
        rows = conn.execute(_SELECT_GOLDSET.format(ph=ph), GOLDSET_CORPS * 2).fetchall()
    elif args.scope == "all-annual":
        rows = conn.execute(_SELECT_ALL_ANNUAL).fetchall()
    else:
        rows = conn.execute(_SELECT_ALL).fetchall()

    # dry-run은 부수효과 없음 — 스토어 파일이 없으면 만들지 않는다 (Codex 리뷰)
    if args.dry_run and not args.out.exists():
        store, done = None, set()
    else:
        store = V.open_store(args.out)
        done = V.existing_ids(store, model_name)
    todo = [r for r in rows if r["section_id"] not in done]
    if args.limit:
        todo = todo[: args.limit]

    print(f"대상 {len(rows)}개 · 완료 {len(done)}개 · 남음 {len(todo)}개 "
          f"· 예상 {len(todo) / args.req_per_min:.0f}분 ({args.req_per_min} req/min)",
          flush=True)
    if args.dry_run or not todo:
        return 0

    t0 = time.time()
    pacer = Pacer(args.req_per_min)
    pending: list[tuple] = []
    n_ok, n_fail = 0, 0
    for idx, r in enumerate(todo):
        text = embed_text(r, args.max_chars)
        vec = None
        for attempt in range(5):
            pacer.wait()
            try:
                vec = emb.embed([text])[0]
                break
            except Exception as exc:
                msg = str(exc)
                # 🔴 영구 오류(400/401/403 등)는 재시도 무의미 — 즉시 중단해야
                #    잘못된 키/모델에서 섹션당 135초를 낭비하지 않는다 (Codex 리뷰)
                retryable = ("429" in msg or "transport" in msg or "5xx" in msg
                             or " 50" in msg)
                if not retryable or attempt == 4:
                    print(f"  🔴 embed 포기({msg[:80]})", flush=True)
                    break
                wait = min(60, 5 * 2 ** attempt)
                print(f"  ⚠️ embed 재시도 대기 {wait}s ({msg[:60]})", flush=True)
                time.sleep(wait)
        if vec is None:
            n_fail += 1
            if n_fail >= 20 and n_ok == 0:   # 시작부터 전멸 = 설정 오류, 조기 종료
                print("  🔴 연속 실패 20건 — 키/모델 설정 확인 필요, 중단", flush=True)
                break
            continue
        pending.append((r["section_id"], r["corp_code"], r["doc_id"], r["path"],
                        r["title"], r["base_year"], vec))
        n_ok += 1
        if len(pending) >= args.save_every:
            V.save_vectors(store, emb.name, pending)
            pending.clear()
        if n_ok % 500 == 0:
            el = time.time() - t0
            eta = (len(todo) - idx - 1) / max(n_ok / el, 1e-9)
            print(f"  [{n_ok}/{len(todo)}] {el / 60:.1f}분 경과 · ETA {eta / 60:.0f}분",
                  flush=True)
    if pending:
        V.save_vectors(store, emb.name, pending)

    print(json.dumps({
        "ok": n_ok, "failed": n_fail, "elapsed_s": round(time.time() - t0, 1),
        "store": str(args.out), "model": emb.name,
    }, ensure_ascii=False), flush=True)
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
