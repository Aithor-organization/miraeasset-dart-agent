"""동시 요청 부하 테스트 — 주최측이 병렬로 호출할 때를 재현한다.

🔴 **왜 필요한가**

골드셋은 **순차** 호출이라 분당 12회였다. CLOVA 한도가 60회/60,000토큰이므로
1/5만 쓴 셈이다. 그런데 주최측 호출 방식은 공지에 없다 — 병렬이면 사정이 다르다:

  · 동시 N개가 한꺼번에 HCX를 두드리면 한도에 훨씬 빨리 닿는다
  · 선제 페이싱(`Pacer`)이 누적되면 **개별 응답 지연**이 늘어난다
  · 평가 타임아웃 **300초**를 넘기면 그 문항은 0점이다

순차만 검증하고 제출하는 것은 "우리가 부르는 방식으로만 된다"를 확인한 것이다.

**측정 대상**
  1. 동시 요청에서 타임아웃(300s) 초과가 발생하는가
  2. 정확도가 순차 대비 떨어지는가 (429 폐기 → 템플릿 강등은 정확도엔 무해)
  3. 서버가 죽거나 5xx를 내는가

사용:
    python3 eval/load_test.py --concurrency 5 --n 20
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

GOLDSET = Path(__file__).parent / "goldset.jsonl"
TIMEOUT = 300.0          # 주최측과 동일


def ask(url: str, qid: str, question: str) -> dict:
    q = urllib.parse.urlencode({"question_id": qid, "question": question})
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(f"{url}?{q}", timeout=TIMEOUT) as r:
            body = json.load(r)
        return {"ok": True, "elapsed": time.monotonic() - t0,
                "answer": body.get("answer", ""), "status": r.status}
    except Exception as exc:                       # 타임아웃·5xx·연결 끊김
        return {"ok": False, "elapsed": time.monotonic() - t0,
                "error": f"{type(exc).__name__}: {exc}"[:120]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8000/answer")
    ap.add_argument("--concurrency", type=int, default=5)
    ap.add_argument("--n", type=int, default=20, help="총 요청 수")
    ap.add_argument("--seed", type=int, default=20260906)
    args = ap.parse_args()

    items = [json.loads(l) for l in GOLDSET.open(encoding="utf-8") if l.strip()]
    random.Random(args.seed).shuffle(items)
    picked = items[: args.n]

    results: list[dict] = []
    lock = threading.Lock()
    sem = threading.Semaphore(args.concurrency)

    def work(i: int, item: dict) -> None:
        with sem:
            # 🔴 question_id를 매번 바꾼다 — 캐시가 부하를 숨기면 측정이 무의미하다
            r = ask(args.url, f"LOAD-{i}-{int(time.time()*1000)}", item["question"])
            r["kind"] = item.get("kind", "?")
            with lock:
                results.append(r)

    print(f"동시 {args.concurrency} · 총 {len(picked)}건 · 타임아웃 {TIMEOUT:.0f}s")
    t0 = time.monotonic()
    threads = [threading.Thread(target=work, args=(i, it)) for i, it in enumerate(picked)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall = time.monotonic() - t0

    ok = [r for r in results if r["ok"]]
    bad = [r for r in results if not r["ok"]]
    lat = sorted(r["elapsed"] for r in ok)

    print(f"\n성공 {len(ok)}/{len(results)} · 총 소요 {wall:.1f}s "
          f"({len(results)/wall*60:.1f} req/min)")
    if lat:
        print(f"지연: 중앙 {statistics.median(lat):.1f}s · "
              f"p95 {lat[min(int(len(lat)*.95), len(lat)-1)]:.1f}s · 최대 {lat[-1]:.1f}s")
        over = [x for x in lat if x >= TIMEOUT]
        print(f"🔴 타임아웃({TIMEOUT:.0f}s) 초과: {len(over)}건"
              if over else f"✅ 타임아웃 여유: 최대치가 한도의 {lat[-1]/TIMEOUT*100:.0f}%")
    for r in bad[:5]:
        print(f"  ❌ {r['error']}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
