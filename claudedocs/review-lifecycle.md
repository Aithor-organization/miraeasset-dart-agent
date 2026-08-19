# Lifecycle 감사 — 공시 Agent (resilience / trace)

감사 기준: AITHOR Agent Framework `resilience-audit` + `trace-audit`.
전제: 단일 서버 · 읽기 전용 · 단일 테넌트 → 멀티테넌트 요구는 **해당 없음** 처리.

## 1. 회복 5패턴

| 패턴 | 판정 | 근거 |
|---|:---:|---|
| retry_backoff | **부분** | `ratelimit.py:35` `MAX_RETRIES=3` · `:37` `_MAX_WAIT=20.0` · `clova.py:87` 429/5xx만 재시도 · 읽기전용이라 멱등 ✅ / **jitter 없음** ❌ (§2) |
| timeout_cancel | **부분** | `clova.py:43` 호출별 timeout ✅ / **전역 데드라인 없음** ❌ — SPEC AC-API4의 `REQUEST_TIMEOUT_S`는 `config.py:47,71`에서 로드만 되고 **코드 어디서도 읽히지 않는다** (전수 grep: `SPEC.md:309` + `config.py` 2곳뿐) |
| fallback_degradation | **부분** | `narrate.py:158-165` 3중 폴백 ✅ / 지표 노출 ❌ (§4) |
| circuit_breaker | **없음** | 연속 실패 상태 부재. `clova.py:83` `observe()`가 **성공 응답에서만** 호출 → 429 지속 시 매 질의가 4회 재시도를 전액 소진. RUNBOOK.md:158이 부재를 자인 |
| saga_compensation | **해당 없음** | `server.py:39` `connect(read_only=True)` · 외부 상태 변경 0건 |

## 2. 🔴 jitter — 없음. thundering herd 위험 높음

`ratelimit.py:61-74` `retry_wait()`는 서버 헤더값에 **고정 `+0.5`** 를 더한다(`:73`). 난수 호출이 파일 전체 0건. 폴백 경로 `min(5.0*2**attempt, 20)`(`:74`)도 결정론적.

→ 동시 요청 N개가 **같은** `x-ratelimit-reset-*` 를 읽어 **같은 순간 깨어난다.** 확률적 충돌이 아니라 결정론적 동기화다. `Pacer.observe()`(`:95-100`)도 동일 헤더로 동기화되며, `_sleep_until`은 락 없이 스레드풀 워커가 공유한다(sync endpoint `server.py:135`) — `wait()`의 0 리셋이 타 워커 페이싱을 지운다.

## 3. 🔴 최악 지연 = 340초/호출 · 질의당 680초 (예산의 227%)

| 항목 | 값 | 위치 |
|---|---:|---|
| connect 5 + read 60 | 65s/시도 | `clova.py:43` |
| 시도 수 `range(MAX_RETRIES+1)` | 4회 | `clova.py:67` |
| 재시도 sleep 상한 | 20s × 3 | `clova.py:96` |
| Pacer 선제 대기 | 20s × 1 | `clova.py:68` |

**`chat()` 1회 = 20 + (65×4) + (20×3) = 340초 (예산 113%).**
질의당 LLM 호출 최대 2회 — 목차 라우팅(`orchestrator.py:188`) + 서술(`:388`) → **680초 = 300초 예산의 227%.**
Pacer·connect를 빼도 `60×4+20×3 = 300초`로 **1회 호출만에 예산 전액**, 여유 0.

🔴 **RUNBOOK.md 정정 필요 2건**: `:49` "요청당 60초 내 종료" · `:78` "실측 최악 163초(54%)". 후자는 **429가 없던 실행의 관측치**로, 재시도 경로가 열리지 않아 상한을 증명하지 않는다. 재시도는 평가일 부하에서만 열린다.

## 4. 🔴 조용한 저하 — 그렇다 (운영 지표 축 무음)

| 노출 경로 | 상태 |
|---|:---:|
| `think_trace` `[3-b] 서술 —` | ✅ `orchestrator.py:389` |
| **`confidence` 필드** | ❌ `:441` `facts and rep.ok`만 본다 → LLM이 죽어도 `"high"` |
| **카운터 / `/metrics`** | ❌ **전무** (서빙 경로 grep 0건) |
| `/ready`·`/meta` `notes` | ❌ `server.py:29,113,120` — **기동 시 스냅샷**. 런타임 강등 미반영 |

🔴 RUNBOOK.md:34는 *"`notes`가 비어있는지가 핵심 지표"* 라 하지만, `notes`는 startup에만 채워진다. **평가 중 발생한 429·절단 강등은 `notes`에 영원히 나타나지 않는다.** 런북이 지시하는 감시 지표가 실제로는 그 사건을 관측하지 못한다.

대안 경로는 `grep '서술 생성 실패' <서버로그>`(RUNBOOK:64)뿐인데, 로그는 stdout only(`server.py:25`)이고 Dockerfile:54에 로그 드라이버 지정이 없다 — grep 대상 파일이 보장되지 않는다.

## 5. 킬스위치 4문항 (RUNBOOK.md §2 반영)

| 문항 | 답 | 근거 |
|---|:---:|---|
| 도구별·행동유형별 즉시 중지 | **부분 예** | LLM 계층 정지 절차 문서화(RUNBOOK:44) — 단 `CLOVA_API_KEY` 제거 + **재기동** 필요. 런타임 토글 0개(환경변수 7개 전부 기동 시 1회 평가, `config.py:65-82`). "즉시"는 아님. 테넌트별은 **해당 없음** |
| 자격증명·네트워크 독립 차단 | **예** | RUNBOOK:46-47. 401 → `ClovaError` → `narrate.py:158-162` 폴백으로 서비스 생존 |
| 일시정지 + 체크포인트 | **아니오** | RUNBOOK:49 자인. 단 근거로 든 "60초 내 종료"는 틀렸다(§3) — 요청이 최악 680초 살아있으므로 중단점 부재의 대가가 런북 서술보다 크다 |
| 자원·변경 재구성 | **해당 없음(쓰기)** / **부분(질의)** | 읽기 전용이라 변경 자원 0. 질의 재구성은 `_cache` 프로세스 메모리(`orchestrator.py:141`) + stdout 로그 의존, **응답 영속 0건** |

## 6. trace span 결손

| span | 빠진 필드 |
|---|---|
| model | **`finish_reason`** 🔴 · `model` · `prompt_version` · in/out 토큰 분리 · LLM 구간 latency |
| run | **`agent_version`** 🔴 (`server.py:27` `version="0.1.0"`이 응답 미포함) · `agent_id` |
| tool | `tool_version` · 도구별 `latency_ms` |

채워진 것: `run_id`(=question_id) · `outcome`(`abstained`/`abstain_reason`/`confidence`/`verification`) · 요청 `latency_ms`(`orchestrator.py:265`) · `tool_name`+인자+result 건수(`_trace_exec`).
**해당 없음**: `parent_run_id`(단일 레벨) · `tenant`/`user_id`(단일) · `risk_tier`(읽기 전용 단일 권한) · `state_changed`.

🔴 **`finish_reason` 결손의 실제 대가**: `clova.py:135`가 읽어 `truncated` bool로 접고(`provider.py:24`), `narrate.py:165`가 "절단/빈응답"으로 뭉갠다. **"stop인데 빈 응답"과 "length 절단"을 사후에 가를 수 없다.** HCX-007 추론 절단은 이 시스템의 알려진 함정(`clova.py:15-25`)이고 RUNBOOK:72가 그 빈도에 따라 `max_tokens` 상향을 지시하는데, **그 판단에 필요한 신호를 코드가 버린다.**

## 배포 전 반드시 고칠 것

### 1. 전역 데드라인 배선 (치명)

`orchestrator.answer()` 진입 시 deadline 설정 → `narrate`/`route_section`에 잔여 예산 전달 → 부족하면 LLM 스킵 후 템플릿 반환. 동시에 `MAX_RETRIES` 3→2(`ratelimit.py:35`), `read` 60→25(`clova.py:43`) → 최악 `2×(30+25×3+20×2) ≈ 290초`로 예산 내 진입.
LLM은 보강재이므로 재시도 축소의 정확도 손실은 0이다 — LLM 전면 차단에도 177/177(README:288-292).

### 2. jitter 추가 (3줄)

`retry_wait()` 반환에 `random.uniform(0, min(wait*0.3, 5))` 가산, `Pacer.observe()`에도 동일. 동시 요청의 결정론적 동기화 제거.

### 3. 강등 가시화 + `finish_reason` 보존

- 응답에 `degraded: bool` / `degrade_reason` 추가, 강등 시 `confidence` 한 단계 하향(`orchestrator.py:441`)
- 프로세스 카운터(`llm_calls`/`llm_degraded`/`llm_429`)를 `/ready`에 노출 → RUNBOOK:34·147의 감시 지시가 비로소 작동
- `LLMResponse`에 `finish_reason` 원문 보존(`provider.py`) → RUNBOOK:72의 진단이 가능해짐

**차순위**: `agent_version` 응답 포함 · Docker 로그 드라이버 명시(RUNBOOK grep 절차의 전제) · RUNBOOK.md:49,78 지연 수치 정정.

## 최종 판정 — 조건부 GO

정확도 축은 견고하다. 결정론 fast-path가 정답 주체라 LLM 전면 차단에도 만점이 유지되고, RUNBOOK.md가 부재 항목(circuit breaker/saga/canary/SLO)을 숨기지 않고 자인한다.
그러나 **회복 메커니즘 자체가 유일한 치명 실패 모드를 만든다** — 재시도·페이싱이 지연을 340~680초로 부풀리는데 이를 자르는 데드라인이 없고, SPEC이 규정한 상수는 배선되지 않았다. 429가 나는 순간 정확도가 아니라 **타임아웃으로 0점**이다. 위 3건 수정 후 GO.

## 검증 수준

| 주장 | 수준 | 근거 |
|---|---|---|
| 최악 340s/680s | [검증됨] | `clova.py:43,67,96` + `ratelimit.py:35,37,99` 상수 직접 합산 |
| `REQUEST_TIMEOUT_S` 미배선 | [검증됨] | 전수 grep 3곳(정의 2 + SPEC 1) |
| jitter 부재 | [검증됨] | `ratelimit.py:61-74` 전문, 난수 호출 0건 |
| `notes`가 startup 전용 | [검증됨] | `server.py:29,41,67,76,90,95` 전부 `_startup()` 내부 |
| 강등 카운터 부재 | [검증됨] | 서빙 경로 `Counter\|prometheus\|/metrics\|degraded` grep 0건 |
| `finish_reason` 미노출 | [검증됨] | `clova.py:135` → `provider.py:24` bool 축약 |
| 재시도 축소의 정확도 무영향 | [추정] | README:288-292 LLM 차단 실측에서 역산, 해당 조합 직접 미측정 |
| Pacer 스레드 경합 | [추정] | 락 부재는 [검증됨], 오작동 재현 미실시 |
