#!/usr/bin/env python3
"""Gold Set 채점 — 실행 중인 서버에 질의하고 결과를 대조한다.

🔴 HTTP로 때린다. 오케스트레이터를 직접 부르면 API 계층(파라미터 파싱·직렬화·
예외 변환)이 빠지는데, 평가는 그 계층을 통과한다.

사용:
    python3 run_server.py &                      # 별도 터미널
    python3 eval/score.py --url http://localhost:8000
    python3 eval/score.py --kind scope_split     # 유형만
    python3 eval/score.py --report eval/last.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

# ── 채점기 ──────────────────────────────────────────────────────────────────

# 답변에서 숫자를 뽑는다. 쉼표 구분 정수와 소수 모두.
_NUM = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def numbers_in(text: str) -> set[int]:
    """답변에 등장하는 모든 수치를 **원 단위 정수 후보**로 환산한다.

    시스템은 `300,870,903백만원(300.9조원)` 처럼 여러 표기를 함께 쓴다.
    표기를 강제하면 채점이 서식 검사가 되어버리므로, 등장 숫자에
    단위 배수를 곱한 후보를 모두 만들어 하나라도 맞으면 정답으로 본다.
    """
    out: set[int] = set()
    for m in _NUM.finditer(text):
        raw = m.group().replace(",", "")
        try:
            v = float(raw)
        except ValueError:
            continue
        tail = text[m.end():m.end() + 3]
        # 숫자 바로 뒤 단위어를 보고 배수를 정한다
        if tail.startswith("조"):
            mults = [10**12]
        elif tail.startswith("억"):
            mults = [10**8]
        elif tail.startswith("백만"):
            mults = [10**6]
        elif tail.startswith("천원"):
            mults = [10**3]
        elif tail.startswith("원"):
            mults = [1]
        else:
            # 단위어가 없으면 흔한 배수를 모두 후보로 (원문 인용 등)
            mults = [1, 10**6]
        for k in mults:
            out.add(int(round(v * k)))
    return out


def value_ok(expect: int, answer: str, *, tol: float = 0.005) -> bool:
    """기대값이 답변에 있는가. 반올림 표기를 허용한다 (300.9조 ≈ 300,870,903백만).

    tol=0.5% — `fmt_krw`가 소수점 1자리로 줄이면 최대 0.05% 오차가 난다.
    0.5%면 표기 반올림은 통과시키고 다른 수치(부문별 매출 등)는 거른다.
    """
    if expect is None:
        return False
    for got in numbers_in(answer):
        if got == expect:
            return True
        if expect != 0 and abs(got - expect) / abs(expect) <= tol:
            return True
    return False


_CONTRACT = ("question_id", "question", "retrieved_context", "think_trace", "answer")


def grade(item: dict, resp: dict, elapsed_ms: float | None = None) -> tuple[bool, str]:
    """(정답 여부, 사유). 사유는 실패 분석용이라 구체적으로 적는다."""
    answer = resp.get("answer") or ""
    abstained = bool(resp.get("abstained"))
    reason = resp.get("abstain_reason")
    cites = resp.get("citations") or []
    ctx = resp.get("retrieved_context") or ""

    # ── boundary — 깨지지 않는가를 본다 (값이 아니라 계약) ──
    #    AITHOR eval-audit의 '경계' 필수 케이스. 빈 입력·상한 초과·무의미 입력에서
    #    5필드를 유지하며 200으로 답하는지가 기준이다.
    #    🔴 422로 계약이 통째로 빠진 실사고(2026-08-23, 810자 질의)에서 왔다.
    if item["kind"] == "boundary":
        missing = [k for k in _CONTRACT if k not in resp]
        if missing:
            return False, f"계약 필드 누락: {missing}"
        if item.get("expect_abstain") and not abstained:
            return False, f"기권해야 하는데 답변함: {answer[:50]}"
        if item.get("forbid_abstain") and abstained:
            return False, f"답해야 하는데 기권함: {reason}"
        return True, f"계약 유지 (기권={abstained}, 사유={reason})"

    # ── regression — 과거 사고가 되살아났는지 본다 ──
    #    정답 여부는 base_kind로 채점하고, 사고의 축(보통 지연)을 추가로 건다.
    if item["kind"] == "regression":
        cap = item.get("max_latency_ms")
        if cap is not None and elapsed_ms is not None and elapsed_ms > cap:
            return False, (f"회귀: {elapsed_ms / 1000:.0f}s > 상한 {cap / 1000:.0f}s "
                           f"— {item.get('meta', {}).get('incident', '')}")
        base = dict(item, kind=item["meta"]["base_kind"])
        ok, why = grade(base, resp)
        return ok, (f"회귀 없음 ({why}"
                    + (f", {elapsed_ms / 1000:.0f}s)" if elapsed_ms is not None else ")"))

    # ── 기권이 정답인 문항 ──
    if item.get("expect_abstain"):
        if not abstained:
            return False, f"기권해야 하는데 답변함: {answer[:60]}"
        want = item.get("expect_reason")
        if want and reason != want:
            # 사유가 달라도 기권 자체는 맞음 — 부분 정답으로 보되 기록은 남긴다
            return True, f"기권 O (사유 다름: {reason} ≠ {want})"
        return True, f"기권 O ({reason})"

    # ── 답해야 하는 문항인데 기권한 경우 ──
    if abstained:
        return False, f"답해야 하는데 기권함: {reason}"

    # ── 유형별 ──
    kind = item["kind"]

    if kind in ("single_value", "scope_split", "basis_split", "event"):
        exp = item["expect_value_krw"]
        if not value_ok(exp, answer):
            rej = item.get("reject_value_krw")
            if rej is not None and value_ok(rej, answer):
                # 🔴 가장 값진 실패 신호 — 반대쪽 값을 가져왔다
                return False, f"반대 값 반환 (누적↔당기 또는 연결↔별도 혼동)"
            return False, f"값 불일치 (기대 {exp:,})"
        return True, "값 일치"

    if kind == "comparison":
        want = item["expect_contains"][0]
        loser = item["meta"]["b"] if want == item["meta"]["a"] else item["meta"]["a"]
        if want not in answer:
            return False, f"정답 기업({want}) 미언급"

        # 🔴 명시적 승자 선언이 있으면 그것이 정본이다.
        #    순서 휴리스틱만 쓰면 질문을 되풀이하는 도입부에 오탐한다 —
        #    "삼성중공업과 SK텔레콤 중 … 더 큰 기업은 SK텔레콤입니다"는
        #    **정답인데** 패자가 먼저 나온다는 이유로 오답 처리됐다 (실측).
        for pat in (r"더\s*(?:큰|높은|많은|우위인)\s*(?:기업|회사|곳)(?:은|는|이)?\s*([^\s,.]+)",
                    r"([^\s,.]+?)(?:이|가)\s*(?:더\s*)?(?:큽|높|많|우위)"):
            m = re.search(pat, answer)
            if m:
                named = m.group(1)
                if want in named:
                    return True, "비교 정확 (명시 선언)"
                if loser in named:
                    return False, f"승자 오판 ({loser}을 더 크다고 선언)"

        # 선언 문형이 없을 때만 순서로 추정한다
        if loser in answer and answer.index(loser) < answer.index(want):
            return False, f"순서 역전 의심 ({loser}이 먼저 언급됨)"
        return True, "비교 정확"

    if kind == "delta":
        want = item["expect_direction"]
        other = "감소" if want == "증가" else "증가"
        if want in answer and other not in answer:
            return True, f"방향 정확 ({want})"
        if other in answer:
            return False, f"방향 반대 ({other} 반환, 정답 {want})"
        return False, "증감 방향 미언급"

    if kind == "section":
        want = item["expect_section"]
        # 인용 또는 근거 문맥에 해당 섹션 주소가 있으면 정답
        paths = [c.get("section") or "" for c in cites]
        if any(p == want or p.startswith(want + "-") for p in paths):
            return True, f"섹션 인용 정확 ({want})"
        if want in ctx:
            return True, f"근거 문맥에 {want} 포함"
        return False, f"섹션 불일치 (기대 {want}, 인용 {paths[:3]})"

    return False, f"미지원 유형: {kind}"


# ── 실행 ────────────────────────────────────────────────────────────────────


def ask(url: str, qid: str, question: str, timeout: float) -> dict:
    """응답 본문을 돌려준다. 🔴 4xx도 예외로 만들지 않는다.

    boundary 케이스는 **비정상 상태코드에서 계약이 유지되는지**가 관심사라,
    HTTPError로 던져버리면 정작 봐야 할 본문을 못 본다.
    """
    qs = urllib.parse.urlencode({"question_id": qid, "question": question})
    req = urllib.request.Request(f"{url.rstrip('/')}/answer?{qs}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = json.loads(r.read().decode("utf-8"))
            return {**body, "_http": r.status}
    except urllib.error.HTTPError as exc:          # 4xx/5xx — 본문을 살려서 채점한다
        raw = exc.read().decode("utf-8", "replace")
        try:
            return {**json.loads(raw), "_http": exc.code}
        except json.JSONDecodeError:
            return {"_http": exc.code, "_raw": raw[:200]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--goldset", type=Path, default=Path("eval/goldset.jsonl"))
    ap.add_argument("--kind", help="특정 유형만 채점")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--timeout", type=float, default=300.0, help="주최측과 동일 (300초)")
    ap.add_argument("--report", type=Path, help="상세 결과 JSON 저장")
    args = ap.parse_args()

    items = [json.loads(l) for l in args.goldset.open(encoding="utf-8")]
    if args.kind:
        items = [i for i in items if i["kind"] == args.kind]
    if args.limit:
        items = items[: args.limit]

    print(f"채점 시작 — {len(items)}문항 · {args.url}\n")

    results = []
    by_kind: dict[str, list[bool]] = defaultdict(list)
    lat: list[float] = []
    t_all = time.time()

    for n, item in enumerate(items, start=1):
        t0 = time.time()
        try:
            resp = ask(args.url, item["question_id"], item["question"], args.timeout)
            err = None
        except Exception as exc:
            resp, err = {}, f"{type(exc).__name__}: {exc}"
        ms = (time.time() - t0) * 1000
        lat.append(ms)

        if err:
            ok, why = False, f"요청 실패: {err}"
        else:
            ok, why = grade(item, resp, elapsed_ms=ms)

        by_kind[item["kind"]].append(ok)
        results.append({**item, "ok": ok, "why": why, "latency_ms": round(ms),
                        # 🔴 답변 전문과 근거를 남긴다 — `eval/faithfulness.py`가
                        #    이 둘로 근거 충실성을 채점한다. 200자로 자르면
                        #    뒷부분 주장이 통째로 채점에서 빠진다.
                        "got_answer": resp.get("answer") or "",
                        "got_context": resp.get("retrieved_context") or ""})

        mark = "✅" if ok else "❌"
        print(f"  {mark} [{n:>3}/{len(items)}] {item['question_id']:<9} {why}")
        if not ok and not err:
            print(f"      Q: {item['question'][:70]}")
            print(f"      A: {(resp.get('answer') or '')[:70]}")

    # ── 요약 ──
    total = len(results)
    passed = sum(r["ok"] for r in results)
    print(f"\n{'=' * 62}")
    print(f"전체 {passed}/{total} = {passed / total * 100:.1f}%"
          f"   ({time.time() - t_all:.0f}초)")
    print(f"{'=' * 62}")
    print(f"{'유형':<16}{'통과':>8}{'정확도':>10}")
    print("-" * 62)
    for kind, oks in sorted(by_kind.items(), key=lambda kv: sum(kv[1]) / len(kv[1])):
        rate = sum(oks) / len(oks) * 100
        bar = "█" * int(rate / 5)
        print(f"{kind:<16}{sum(oks):>4}/{len(oks):<3}{rate:>8.1f}%  {bar}")
    print("-" * 62)
    lat.sort()
    print(f"지연 중앙값 {lat[len(lat) // 2]:.0f}ms · 최대 {lat[-1]:.0f}ms "
          f"(주최측 타임아웃 300,000ms)")

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps({
            "total": total, "passed": passed,
            "by_kind": {k: {"passed": sum(v), "total": len(v)} for k, v in by_kind.items()},
            "results": results,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n상세 → {args.report}")

    # 실패 사유 집계 — 무엇부터 고칠지 정하는 근거
    fails = [r for r in results if not r["ok"]]
    if fails:
        print(f"\n실패 {len(fails)}건 사유별:")
        agg: dict[str, int] = defaultdict(int)
        for r in fails:
            key = r["why"].split("(")[0].split(":")[0].strip()
            agg[key] += 1
        for why, cnt in sorted(agg.items(), key=lambda kv: -kv[1]):
            print(f"  {cnt:>3}건  {why}")

    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
