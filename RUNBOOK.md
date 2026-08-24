# 운영 Runbook — 공시 Agent

> AITHOR Agent Framework `agentize-check`가 **BLOCKED**을 낸 이유가 이 문서의 부재였다:
> *"operations: name one accountable owner and write the runbook before launch"*.
> 프레임워크 `resilience-audit` 킬스위치 체크리스트를 이 문서의 뼈대로 쓴다.

## 0. 책임자

| 역할 | 담당 | 연락 |
|---|---|---|
| 최종 책임자 (accountable) | 제출자 본인 | — |
| 평가 기간 대기 | 제출자 본인 | 09-06 제출 후 심사 종료까지 |

🔴 **1인 프로젝트다.** RACI를 형식적으로 채우지 않는다 — 책임자는 한 명이고, 그 사실을
적어두는 것이 목적이다(프레임워크 `governance-audit`가 요구하는 것은 이름이지 조직도가 아니다).

---

## 1. 정상 상태는 무엇인가

```bash
curl -s http://<host>/health   # {"status":"ok"}
curl -s http://<host>/ready    # ready:true · sections_indexed:112797 · notes:[]
curl -s http://<host>/meta     # llm:"clova" (키 없으면 "stub")
```

| 신호 | 정상 | 의미 |
|---|---|---|
| `/ready.ready` | `true` | 인덱스 로드 완료. `false`면 아직 기동 중이거나 인덱스 경로 문제 |
| `/ready.sections_indexed` | 112,797 | 다르면 인덱스가 부분 빌드다 — §4 참조 |
| `/ready.notes` | `[]` | 비어있지 않으면 **강등 상태**. 무엇이 꺼졌는지 문자열로 나온다 |
| `/meta.llm` | `clova` | `stub`이면 CLOVA 키 미설정 → 서술·목차 라우팅 없이 결정론만 |

🔴 **`notes`가 비어있는지가 핵심 지표다.** 답변은 계속 나오지만 품질 계층이 꺼져 있을 수 있다.

---

## 2. 킬스위치 — 무엇을, 어떻게 끄는가

프레임워크 체크리스트의 4문항에 대한 실제 답이다. **없는 것은 없다고 쓴다.**

| 스위치 | 방법 | 있는가 |
|---|---|---|
| **LLM 계층만 정지** (서술·목차 라우팅) | `.env`의 `CLOVA_API_KEY` 줄 삭제 후 재기동 → StubProvider 폴백 | ✅ |
| **서비스 전체 정지** | `pkill -f run_server.py` (또는 컨테이너 stop) | ✅ |
| **자격증명 폐기** | NCP 콘솔 → CLOVA Studio 키 재발급 (서버 접근 불필요) | ✅ 모델 호출과 독립 |
| **네트워크 차단** | NCP ACG 아웃바운드 규칙 제거 | ✅ 모델 호출과 독립 |
| 도구별·테넌트별 개별 정지 | — | ❌ **해당 없음** — 단일 테넌트·읽기 전용이라 분리할 축이 없다 |
| 실행 중 작업 일시정지 + 체크포인트 | — | ❌ **없음.** 요청당 처리가 60초 내에 끝나는 stateless 응답이라 중단점이 없다. 죽이면 그 요청만 실패한다 |

🔴 **LLM 정지가 서비스 정지가 아니다.** 이게 이 시스템의 핵심 운영 성질이다 —
LLM을 완전히 끈 상태에서도 골든셋 177/177(100%)이 나온다. **HCX가 이상하면 키를 빼고 재기동하는 것이
가장 빠른 완화**이며, 정확도는 잃지 않는다.

⚠️ **셸에서 `unset CLOVA_API_KEY`만 하면 안 꺼진다.** `run_server.py`가 `.env`를 직접 읽으므로
파일에 키가 남아 있으면 그대로 살아난다. 반대 방향도 마찬가지다 — 킬스위치를 쓴 뒤
**되살릴 때는 `.env` 한 줄을 복구**하면 된다. 기동 로그 끝의 `llm=clova` / `llm=stub`이
지금 어느 상태인지 알려주는 유일한 신호다.

---

## 3. 장애 시나리오별 대응

### 3-1. 답변이 전부 템플릿 문장으로 나온다 (문장이 딱딱함)

**원인**: HCX 강등. 정확도 문제는 아니다.

```bash
grep -c '서술 생성 실패' <서버로그>      # 429 또는 API 오류 횟수
grep '서술 생성 실패' <서버로그> | tail -3   # 사유 확인
```

| 사유 | 대응 |
|---|---|
| `clova 429` | **정상 동작이다.** 재시도·선제 페이싱이 작동 중. 부하가 끝나면 회복 |
| `clova 401` | 키 만료/오타 → `.env` 확인 후 재기동 |
| `LLM 응답 불가(절단)` | HCX 추론이 `max_completion_tokens`에 잘림. 빈도 높으면 `narrate.py` `max_tokens` 상향 |

🔴 **조치하지 않아도 답변은 정확하다.** 서두르지 말 것.

### 3-2. 응답이 느리다 / 타임아웃이 난다

평가 타임아웃은 **300초**다. 실측 최악값은 동시 50 요청에서 163초(54%).

```bash
grep -c '선제 대기' <서버로그>    # rate limit 페이싱 발동 횟수
```

- 선제 대기가 많다 = 토큰 한도(60,000/분)에 닿았다는 뜻. **요청 수가 아니라 토큰이 병목**이다.
- 300초에 근접하면: `CLOVA_API_KEY`를 빼서 LLM 계층을 끈다 → 지연 중앙값 10.4초 → **0.00초**.

### 3-3. `/ready.ready == false`

```bash
ls -la index/dart.sqlite            # 인덱스 파일 존재 확인
curl -s http://<host>/meta | head   # 로드 실패 사유
```

인덱스가 없거나 깨졌으면 §4.

### 3-4. 답변에 근거(citations)가 비어 있다

기권 응답이면 **정상**이다(투자의견·개인정보·기간 밖). `abstained: true`를 확인하라.
일반 답변인데 비어 있으면 `_cited_only` 필터 회귀 — `tests/unit/test_answer_quality.py` 실행.

---

## 4. 인덱스 재빌드

```bash
python3 scripts/build_index.py     # 코퍼스 → SQLite + FTS5, 수십 분 소요
curl -s http://<host>/ready        # sections_indexed 확인
```

🔴 **평가 기간에는 하지 마라.** 재빌드 중 `/ready`가 false가 되고 답변이 전부 실패한다.
사전에 빌드해 두고, 서버에는 **완성된 인덱스를 올린다**.

### 4-1. 벡터 스토어 (하이브리드 검색용, 선택)

🔴 **`index/`는 `.gitignore` 대상이다 — 저장소에 없다.** `dart.sqlite`와 마찬가지로
**서버에 파일을 직접 올려야 한다.** 배포 시 빠뜨리기 쉬운 지점이므로 여기 명시한다.

```bash
python3 scripts/embed_sections.py --scope all-annual --dry-run   # 대상/시간 확인
python3 scripts/embed_sections.py --scope all-annual             # 중단돼도 재실행하면 이어서 함
scp index/embeddings.sqlite <서버>:<배포경로>/index/             # dart.sqlite와 같은 위치
```

- 파일이 없거나 `DART_HYBRID`가 꺼져 있으면 **BM25 단독으로 자동 강등** — 서버는 정상 동작한다.
  `/ready`의 `notes`에 강등 사유가 표시된다.
- 활성화: 서버 환경변수 `DART_HYBRID=1`. 활성화 전 골든셋을 한 번 돌려 회귀가 없는지 볼 것.
- 범위: `goldset-corps`(파일럿 ~2.9K) · `all-annual`(70개사 최신 사업보고서 ~10K, 약 2시간) ·
  `all`(전 유효문서 ~102K, 약 30시간).

---

## 5. 롤백

배포 단위가 git 커밋 + 인덱스 파일 두 개뿐이다.

```bash
git log --oneline -5
git checkout <직전_커밋>
pkill -f run_server.py && python3 run_server.py    # 재기동
python3 eval/score.py --report /tmp/rollback_check.json   # 골든셋으로 확인
```

**롤백 판단 기준**: 골든셋 177문항 중 **170 미만**이면 되돌린다(기준선 177/177, 허용 4%).
프레임워크 `eval-audit` 게이트 4축 중 quality 허용치는 0%이지만, 단일 실행의 LLM 변동을
감안해 4%를 둔다 — **이 완화는 의도적이며 그 사실을 여기 남긴다**.

---

## 6. 평가 직전 체크리스트

```bash
# 1) 테스트
python3 -m pytest tests/ -q

# 2) 골든셋 (LLM 포함)
python3 eval/score.py --report /tmp/pre_eval.json

# 3) 콜드 캐시 워밍업 — 첫 요청이 최대 1.5초 걸린다
for q in "삼성전자 2024년 매출액은?" "SK하이닉스 영업이익은?" "배당에 관한 사항"; do
  curl -s -G http://<host>/answer --data-urlencode "question_id=warm" --data-urlencode "question=$q" > /dev/null
done

# 4) 강등 상태 확인
curl -s http://<host>/ready | grep -o '"notes":\[[^]]*\]'   # [] 여야 한다
```

---

## 7. 이 문서가 답하지 못하는 것

프레임워크 `resilience-audit`·`autonomy-gate` 기준 대비 **없는 것**을 숨기지 않는다:

| 항목 | 상태 |
|---|---|
| 회로 차단기 (circuit breaker) | ❌ 없음. LLM 실패는 요청 단위로 템플릿 강등되므로 차단할 대상이 없다 |
| Saga / 보상 트랜잭션 | ❌ **해당 없음** — 쓰기 작업이 0건인 읽기 전용 시스템 |
| 카나리 배포 | ❌ 없음. 서버 1대·평가 기간 한정이라 트래픽 분할 대상이 없다 |
| 오류 예산 / SLO | ❌ 없음. 평가 기간이 짧아 예산을 소진할 시간 창이 없다 |
| 독립 제3자 평가 | ⏳ **주최측 심사가 그 역할**이다 |
| 실행 중 체크포인트 | ❌ 없음 (§2 참조) |

🔴 위 항목들은 **프로덕션 서비스라면 결함**이지만, 이 시스템은 심사 기간 한정 단일 서버
읽기 전용 QA다. 그 전제가 바뀌면(상시 서비스 전환) 이 표부터 다시 보라.
