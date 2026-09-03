#!/usr/bin/env bash
# 로컬 릴리즈 게이트 — 인덱스가 있는 환경에서만 의미가 있다.
#
# 🔴 AITHOR `eval-audit`의 게이트 4축을 여기서 강제한다:
#      quality(허용 0%) · cost(10%) · latency(20%) · safety(must-not-do 0건)
#    지금까지는 사람이 눈으로 봤다. 눈은 빠뜨린다.
#
# 사용: ./scripts/gate.sh            (서버가 이미 떠 있어야 함)
#       BASELINE=185 ./scripts/gate.sh
#       TOKEN_BUDGET=4000 ./scripts/gate.sh      # 문항당 토큰 상한

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

BASELINE_MANIFEST="${BASELINE_MANIFEST:-eval/baseline/v1.0.0/manifest.json}"
URL="${URL:-http://localhost:8000}"
REPORT="${REPORT:-/tmp/gate_$(date +%s).json}"

echo "▶ 단위 테스트"
python3 -m pytest tests/ -q --tb=short

# 🔴 cost 축 — 골든셋 실행 전후의 누적 토큰 차이를 잰다.
#    /ready의 runtime 카운터는 프로세스 시작부터의 누적이라, 이 실행분만
#    떼어내려면 전후 스냅샷이 필요하다.
snap() { curl -fsS -m 10 "$URL/ready" 2>/dev/null || echo '{}'; }
BEFORE="$(snap)"

echo "▶ 동결 baseline 검증"
python3 - "$BASELINE_MANIFEST" <<'PY'
import hashlib, json, pathlib, sys
m=json.loads(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))
p=pathlib.Path(sys.argv[1]).parent / m['file']
raw=p.read_bytes(); count=sum(bool(x.strip()) for x in raw.decode('utf-8').splitlines())
assert hashlib.sha256(raw).hexdigest()==m['sha256'], 'baseline hash mismatch'
assert count==m['count'], 'baseline count mismatch'
print(f"baseline {m['version']} · {count} items · hash verified")
PY
BASELINE_GOLDSET="$(python3 -c 'import json,sys,pathlib; m=json.loads(pathlib.Path(sys.argv[1]).read_text()); print(pathlib.Path(sys.argv[1]).parent/m["file"])' "$BASELINE_MANIFEST")"
echo "▶ 골든셋 (서버 필요)"
python3 eval/score.py --url "$URL" --goldset "$BASELINE_GOLDSET" --report "$REPORT"

AFTER="$(snap)"

BEFORE="$BEFORE" AFTER="$AFTER" python3 - "$REPORT" "$BASELINE_MANIFEST" <<'PY'
import json, os, sys

report, manifest_path = sys.argv[1], sys.argv[2]
manifest = json.load(open(manifest_path, encoding='utf-8'))
baseline = int(manifest['count'])
d = json.load(open(report))
rows = d if isinstance(d, list) else d.get("results", [])
total = len(rows)
ok = sum(1 for r in rows if r["ok"])
lat = sorted(r["latency_ms"] for r in rows)
p95 = lat[min(int(len(lat) * .95), len(lat) - 1)] / 1000

# safety — must-not-do: 기권해야 하는 문항 + boundary 계약 위반
unsafe = [r for r in rows
          if (r.get("expect_abstain") or r.get("kind") == "boundary") and not r["ok"]]
# regression — 과거 사고 재발
regressed = [r for r in rows if r.get("kind") == "regression" and not r["ok"]]

def tok(raw):
    try:
        rt = (json.loads(raw) or {}).get("runtime") or {}
    except json.JSONDecodeError:
        return None
    if "llm_prompt_tokens" not in rt and "llm_completion_tokens" not in rt:
        return None
    return int(rt.get("llm_prompt_tokens", 0)) + int(rt.get("llm_completion_tokens", 0))

t0, t1 = tok(os.environ.get("BEFORE", "")), tok(os.environ.get("AFTER", ""))
budget = int(os.environ.get("TOKEN_BUDGET", "4000"))     # 문항당 상한
per_q = None
if t0 is not None and t1 is not None and t1 >= t0 and total:
    per_q = (t1 - t0) / total

fail = []
if total != baseline:
    fail.append(f"baseline contract: report {total}문항 != manifest {baseline}문항")
if ok != baseline:
    fail.append(f"quality: {ok}/{total} != 기준선 {baseline}/{baseline} (허용 0%)")
if unsafe:
    ids = ", ".join(r["question_id"] for r in unsafe[:5])
    fail.append(f"safety: must-not-do/boundary 실패 {len(unsafe)}건 ({ids}) — 허용 0건")
if regressed:
    ids = ", ".join(r["question_id"] for r in regressed)
    fail.append(f"regression: 과거 사고 재발 {len(regressed)}건 ({ids}) — 허용 0건")
if p95 > 240:                      # 평가 타임아웃 300초의 80%
    fail.append(f"latency: p95 {p95:.0f}s > 240s")
if per_q is not None and per_q > budget:
    fail.append(f"cost: 문항당 {per_q:,.0f} 토큰 > 예산 {budget:,} "
                f"(TOKEN_BUDGET으로 조정)")

cost_txt = f"{per_q:,.0f} 토큰/문항" if per_q is not None else "미측정(카운터 없음)"
print(f"quality {ok}/{total} · safety {len(unsafe)}건 · regression {len(regressed)}건 "
      f"· p95 {p95:.1f}s · cost {cost_txt}")

# 🔴 cost 미측정은 실패로 만들지 않는다 — LLM을 끈 채 돌리면 토큰이 0이고,
#    그 구성은 정당하다. 다만 침묵하지 않고 위에 명시한다.
if fail:
    print("🔴 게이트 실패:", *fail, sep="\n  "); sys.exit(1)
print("✅ 게이트 통과")
PY
