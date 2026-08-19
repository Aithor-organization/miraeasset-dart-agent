# Verifier QA 검증 보고서 — miraeassetfesta (DART 공시 Agent)

검증일 2026-08-19 · 실측: `python3 -m pytest tests/ -q` → **242 passed / 0 failed / 41.52s** (직접 실행, exit 0)

---

## 1. 골든 케이스 5종 — `eval/goldset.jsonl` 177건 kind 분포 실측

`kind` 필드는 **질문 유형** 분류이지 케이스 종류가 아니다. 실제 매핑:

| 케이스 | 존재 | 실측 근거 |
|---|:--:|---|
| normal[권장] | ✅ **142** | `single_value` 40 · `section` 25 · `event` 22 · `comparison` 20 · `delta` 15 |
| **boundary[필수]** | ✅ **40** | `scope_split` 20(누적↔당기) · `basis_split` 20(연결↔별도), 전건 `reject_value_krw` 보유 |
| failure[권장] | ❌ **0** | 인덱스 결손·파서 실패·DB 오류 거동 문항 없음 |
| **must_not_do[필수]** | ✅ **55** | `abstain` 15 (`ABS-001` `meta.trap="forbidden_prediction"`) + `reject_value_krw` 40 (`eval/score.py:113` "반대 값 반환") |
| regression[권장] | ⚠️ 골든셋 **0** | pytest 측 회귀 가드 1건: `tests/unit/test_api_and_e2e.py:284` (캐시키) |

🔴 `expect_abstain=true` 15건은 전부 *정책적* 거부(예측 요구)다. *장애* 케이스 0건.

## 2. 채점기 5종 — **LLM-as-judge 미사용**

| 채점기 | 사용 | 근거 |
|---|:--:|---|
| 규칙기반(결정론) | ✅ | `eval/score.py:82 grade()` — 전 유형 정규식·수치 대조 |
| 실행기반(결정론) | ✅ | pytest 242건 + `eval/score.py:260 return 0 if passed==total else 1` |
| 소형분류기 | ❌ | 없음 |
| **LLM-as-judge** | ❌ | **채점 경로 LLM 호출 0건** |
| 사람 | ❌ | `src/`·`tests/`·`eval/` 전수 grep 0건 |

🔴 `comparison` 20문항은 `eval/score.py:127` 정규식 2개로 승자 판정 → 실패 시 `:138` **어순 휴리스틱**으로 강등. 서술 정답성을 어순으로 추정하는데 교정할 judge가 없다.

## 3. 결과 확인 5축

| 축 | 판정 | 근거 |
|---|:--:|---|
| record_exists | ✅ | `eval/score.py:154` 인용 `section` 경로 대조 |
| field_values | ✅ | `eval/score.py:66 value_ok()` tol=0.5% |
| side_effects_bounded | ⚠️ | `/answer` 읽기전용이나 **경계 assert 0건** |
| idempotent_repeat | ⚠️ | `tests/unit/test_api_and_e2e.py:275` 2회 호출 일치 — 단 `:276` 주석대로 **캐시 히트**. 캐시 미스 재계산 동등성 미검증 |
| reversible | ❌ | 미검증 |

## 4. 게이트 4축 — **전부 사람 눈. CI 0**

`.github` 디렉토리 부재. `eval/score.py`는 자기 docstring(`:9-11`) 외 **어떤 스크립트·Dockerfile에서도 호출되지 않음**.

| 게이트 | 상태 |
|---|---|
| quality(허용 0%) | ⚠️ `eval/score.py:260` exit 1 존재하나 **읽는 자동화 없음** |
| cost(10%) | ❌ `src/dart_agent/llm/clova.py:128` `usage` 파싱만, 임계·집계·차단 없음 |
| latency(20%) | ❌ `eval/score.py:237` p50/max **출력만**, assert 없음. `eval/load_test.py` 존재하나 **결과 artifact 0건** |
| safety(must-not-do 0건) | ⚠️ abstain 15/15 통과, CI 미강제 |

## 5. Failure Rules

| # | 규칙 | 판정 | 근거 |
|:-:|---|:--:|---|
| 1 | 필수 파일 누락 | **PASS** | `specs/SPEC.md`·`specs/TASKS.md`·`tests/`·`eval/goldset.jsonl`·`eval/score.py`·`README.md`·`Dockerfile` 존재 |
| 2 | guard/audit/human review 없음 | **FAIL** | guard ✅ (`agent/abstention.py:138` 예측차단 · `agent/orchestrator.py:364` PII게이트 · `agent/verifier.py:95` V1–V5 + `:428` 2-pass 재검증) / **audit ❌** (전 소스 stdout `logging`만, 질의–답변 감사추적 영속화 0건) / **human review ❌** (0건) |
| 3 | requirement without task | **FAIL** | SPEC의 AC 54건 중 **8건이 tests·src 전무**: `AC-API3, AC-API5, AC-C4, AC-S2, AC-S3, AC-TEST7, AC-U2, AC-U3`. 27건은 src 주석에만 존재(테스트 무). `specs/TASKS.md` T001–T051 **14개 전부 `- [ ]` 미체크** |
| 4 | 테스트 미실행 완료 선언 | **PASS** | 242 passed 실측, eval artifact 13개 |

> 규칙 2 판정 근거: "3요소 전부 부재"로 읽으면 PASS. **각 요소 필수**로 읽어 FAIL 처리했다 — 금융 공시 QA에서 감사추적 부재는 오답 사후 재구성 불가를 뜻한다. 반대 해석이어도 최종은 규칙 3 단독으로 FAIL.

---

## 최종 판정: **FAIL**

**가장 중요한 빈 것 3건**

1. **CI 부재 — 게이트 4축 중 2축(cost·latency) 미구현, 나머지 2축은 수동.** exit code를 낼 줄 아는 채점기(`eval/score.py:260`)가 있는데 그것을 읽는 자동화가 저장소에 없다.
2. **골든셋 순환 + 판별력 0.** `eval/generate_goldset.py:11`은 `fact_query` 순환은 끊었으나 **파서→DB 층은 공유** — `parsers/`가 값을 오추출하면 정답과 답변이 같이 틀리고 테스트는 통과한다. DART 원문 대조 정답 0건. 게다가 `eval/no_llm.json` = **LLM 0호출로도 177/177**(README:293). 즉 100%는 결정론 경로 점수이지 에이전트 점수가 아니며, 이 공백을 메울 LLM-judge·사람·failure 케이스가 전부 0이다.
3. **추적 단절.** AC 8건 미커버 + `specs/TASKS.md` 14건 전부 미체크 → 요구사항–구현 추적이 문서상 죽어 있다.

**차단 해제 최소 조건**: ① AC 8건 테스트 추가 + TASKS.md 체크 동기화 ② `eval/score.py`·pytest를 CI에 배선 + latency p95 임계 assert ③ failure 케이스 골든셋 편입 + 질의–답변 감사 로그 영속화.

### 검증 수준

| 주장 | 수준 | 근거 |
|---|---|---|
| 242 passed / 177-177 / no_llm 100% | [검증됨] | 직접 실행 + `eval/v6.json`·`no_llm.json` 파싱 |
| AC 8건 미커버, TASKS 미체크 | [검증됨] | specs/tests/src 전수 정규식 대조 |
| CI·audit·HITL 0건 | [검증됨] | 디렉토리 확인 + 전수 grep |
| load_test 미실행 | [추정] | 결과 artifact 부재 기반. 실행 후 미저장 가능성 배제 못 함 |
| 골든셋 순환 위험 | [추정] | 코드 경로 분석. 실제 파서 오추출 사례 미확인 |
