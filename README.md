# 공시 Agent (Disclosure Analyst)

제10회 2026 미래에셋증권 AI Festival — 공시 데이터 기반 자연어 질의응답 Agent

> **설계 한 줄**: 공시의 **숫자는 조회하고 문장은 생성한다.**
> DART XML의 XBRL 인라인 태깅·정형 필드에서 사실을 먼저 확정하고, LLM은 해석과 서술만 맡는다.
> 수치·계산 경로는 결정론적으로 제한하며, 비수치 서술 위험은 검증기와 회귀 테스트로 관리한다.
>
> 업무 범위·자율성·데이터 경계·표현 규칙은 [`docs/SCOPE_CARD.md`](docs/SCOPE_CARD.md)에 고정한다.

---

## 신뢰·보안 요약

이 시스템은 **공개 DART 코퍼스를 대상으로 한 익명·읽기 전용 QA**다. 송금·주문·메일·공시 발송·외부 데이터 변경 기능은 없으며, 모델이 임의 도구를 선택해 실행하는 자율 루프도 없다. 금융이라는 도메인명보다 실제 부수효과와 데이터 경계를 기준으로 통제한다.

| 보장 목표 | 코드 수준 통제 | 확인 지점 |
|---|---|---|
| 숫자 환각 억제 | 수치·계산은 SQLite fact와 `fact_id` operand만 사용 | `agent/tools.py`, 검증기 V1 |
| 근거 없는 서술 억제 | 인용 실재성·요구사항·금지표현·미해결 슬롯을 결정론적으로 검사 | `agent/verifier.py` V1~V5 |
| 예측·투자 권유 차단 | 미래 전망·목표주가·매수/매도 요구를 코드에서 기권 | `agent/abstention.py` |
| PII·secret 외부 전송 차단 | 질의 해석·검색·LLM 호출 전에 민감 입력 거부 | `agent/pii.py`, `agent/orchestrator.py` |
| 공급자 경계 | 허용된 Naver HTTPS endpoint와 `HCX-*`만 허용 | `config.py` |
| 장애 격리 | 120초 요청 예산·bounded retry·Stub 폴백·`LLM_ENABLED=0` kill switch | `llm/`, `RUNBOOK.md` |
| 메모리 고갈 방지 | 응답 캐시 TTL/LRU, rate-limit client 상태 hard bound | `orchestrator.py`, `api/ratelimit_mw.py` |
| 로그 유출 억제 | 로그 기록 전에 전화·이메일·주민번호·자격증명 마스킹 | `observability.py` |
| 재현 가능한 평가 | 동결 baseline의 SHA-256·문항 수를 검증한 뒤 5축 gate 실행 | `eval/baseline/v1.0.0/`, `scripts/gate.sh` |

모든 정상 응답은 출처와 검증 상태를 포함한다. 요청에는 `trace_id`를 부여하고, 관측에는 원문 대신 `question_hash`를 사용한다. 상세 자율성·데이터·HITL/MCP 경계는 [`docs/SCOPE_CARD.md`](docs/SCOPE_CARD.md), 장애 대응과 롤백은 [`RUNBOOK.md`](RUNBOOK.md)에 있다.

### 신뢰 수준을 읽는 법

- **현재 코드 검증**과 **과거 전체 인덱스 실측**을 구분한다.
- 운영 인덱스가 없는 환경에서 인덱스 의존 골든셋·검색 A/B·부하 결과를 재현했다고 주장하지 않는다.
- `eval/goldset.jsonl`은 작업용 호환 경로이고, 릴리스 기준은 hash로 동결된 `eval/baseline/v1.0.0/manifest.json`이다.
- constrained decoding, OTel backend, 외부 uptime monitor는 현재 필수 경로가 아니다. 출력 화이트리스트·결정론 검증·Docker readiness가 현재 통제다.
- 공인 endpoint는 **배포·실측 완료**다 (§0, 2026-09-03 계약 10종 실호출 검증). 컨테이너 non-root 전환과 TLS 종단은 잔여 작업이다 — 평가는 HTTP로 진행한다.

---

## 0. 평가용 API End-point

<!-- 🔴 제출 필수 항목. 2026-09-02 배포 완료 — 아래는 실제 운영 주소다.
     이 주소는 terraform destroy/apply 시에만 바뀐다. 컨테이너 교체는 주소를 유지한다. -->


```
http://49.50.143.143/answer
```

| 항목 | 값 |
|---|---|
| 프로토콜 | HTTP (표준 포트 80) |
| 경로 | `/answer` (고정) |
| 메서드 | GET |
| 파라미터 | `question_id`, `question` |
| 인증 | 없음 (주최측 무헤더 호출) |

**호출 예**

```bash
curl -G "http://49.50.143.143/answer" \
  --data-urlencode "question_id=Q-001" \
  --data-urlencode "question=삼성전자의 2024년 연결기준 매출액은?"
```

**응답** — 명시 5필드를 항상 포함하며 전부 문자열 타입. 상세는 §2.

> 재현 환경: `requirements.txt` (버전 고정) · `Dockerfile` (선택 제공)
> 전처리 산출물: `index/dart.sqlite` (3.9GB) — 클라우드 스토리지 링크로 별도 제출

---

## 1. 빠른 시작

```bash
# 0) 의존성
python3 -m pip install -r requirements.txt

# 1) 인덱스 빌드 (코퍼스 → SQLite Fact Store + BM25)
#    스모크: 유형별 60건만
python3 scripts/build_index.py --limit 60 --rebuild
#    전체 4,204건
python3 scripts/build_index.py --rebuild

# 2) HyperCLOVA X 키 설정 — .env 한 줄이면 된다 (run_server가 직접 읽는다)
echo 'CLOVA_API_KEY=<발급받은 키>' > .env
#    키가 없어도 서버는 뜬다. 단 결정론 경로(수치·섹션·이벤트)만 동작하고
#    LLM 서술은 비활성이다. 기동 로그 끝의 `llm=` 값으로 확인할 것:
#      llm=clova → 정상   /   llm=stub → 키 미인식

# 3) 서버 실행 (로컬 개발 — 8000)
python3 run_server.py            # http://0.0.0.0:8000
#    배포 시에는 외부를 표준 포트 80으로 노출한다 (docker -p 80:8000, §6 참조)

# 4) 호출 (주최측 평가 계약)
curl -G localhost:8000/answer \
  --data-urlencode "question_id=Q-001" \
  --data-urlencode "question=삼성전자의 2024년 연결기준 매출액은 얼마인가?"

# 5) 테스트
python3 -m pytest

# 6) 릴리스 재현성 manifest 생성
python3 scripts/release_manifest.py --out release-manifest.json
```

### 환경변수

| 변수 | 기본값 | 설명 |
|---|---|---|
| `CLOVA_API_KEY` | (없음) | HyperCLOVA X 키. **없으면 StubProvider로 폴백**하고 서버는 계속 동작 |
| `CLOVA_BASE_URL` | `https://clovastudio.stream.ntruss.com/v1/openai` | OpenAI 호환 엔드포인트 |
| `CLOVA_CHAT_MODEL` | `HCX-007` | 허용된 HCX 모델 ID (provider revision은 릴리스 평가로 감시) |
| `LLM_ENABLED` | `1` | `0`이면 전역 kill switch: 결정론 경로만 사용 |
| `TOKEN_BUDGET` | `4000` | 평가 게이트 문항당 토큰 상한 |
| `CLOVA_EMBEDDING_MODEL` | `bge-m3` | 8,192 tokens / 1024 dim |
| `DART_CORPUS_ROOT` | `docs/3.공시/corpus` | 코퍼스 경로 |
| `DART_DB_PATH` | `index/dart.sqlite` | Fact Store |
| `DART_BM25_PATH` | `index/bm25.pkl` | 레거시 인메모리 BM25 캐시. FTS5 색인이 있으면 사용하지 않는다 |
| `DART_SEARCH_THRESHOLD` | `0.35` | abstention 판정 임계값 |
| `PORT` / `HOST` | `8000` / `0.0.0.0` | |

---

## 2. API

```
GET /answer?question_id={id}&question={질의}
GET /health · /ready · /meta
```

**응답** — 주최측 명시 5필드를 **항상** 포함하고, 부가 필드로 확장한다.

```json
{
  "question_id": "Q-001",
  "question": "삼성전자의 2024년 연결기준 매출액은 얼마인가?",
  "retrieved_context": "[C1] 삼성전자 사업보고서 (2024.12) · 접수번호 20250311001085 · III-2-2\n      매출액 (주29) 300,870,903 백만원 (연결, FY, xbrl)",
  "think_trace": "[1] 질의 해석 … [2] 계획 … [3] 도구 실행 … [4] 검증 … [5] 결론",
  "answer": "삼성전자의 2024년 연결기준 매출액은 300,870,903백만원(300.9조원)입니다 [C1].",
  "citations": [{"id": "C1", "doc_id": "...", "rcept_no": "...", "section": "III-2-2", "source": "xbrl"}],
  "confidence": "high", "abstained": false, "verification": "검증 통과", "latency_ms": 9,
  "trace_id": "b2a9…", "question_hash": "54dce1a9f2b0d8c1"
}
```

🔴 **HTTP 500을 반환하지 않는다.** 모든 내부 예외를 abstention 응답 + 200으로 변환한다 —
평가 중 한 문항의 예외가 서버 장애로 번지면 안 되기 때문.

---

## 3. 아키텍처

```
GET /answer
   │
   ├─ ① Guard        입력 살균 · PII 질의 차단
   ├─ ② Q-이해       기업 별칭(4-way) · 섹터 · 기간 · 연결/별도 · 지표 · 요구사항 분해
   ├─ ③ 도구 라우팅   주소 지정 → 사실 조회 → 검색  (이 순서가 설계 D2)
   │     fact_query · get_section · doc_search · event_query · trace_chain · compute
   ├─ ④ 근거 조립     정정 체인 해소 → 유효본만 · citation id 부여
   ├─ ⑤ 답변 조립     수치는 fact 값 그대로 (D1)
   └─ ⑥ 검증 V1~V5   전부 결정론 — LLM 호출 0건
```

### 3계층 데이터

| 계층 | 내용 | 담당 질의 |
|---|---|---|
| **L1 Fact Store** (SQLite) | XBRL 재무 사실 · 계약/자금조달/지분 이벤트 · 정정 체인 · 별칭 | 수치·집계·비교·순위 |
| **L2 Section Store** | 법정 목차 주소(`II-3`, `III-2-2` …) 트리 | 문서·섹션 특정 조회 |
| **L3 검색** | BM25(Kiwi 형태소, **SQLite FTS5 디스크 색인**) + 벡터(bge-m3) **RRF k=5 w=3 하이브리드가 기본 ON** (2026-08-25 전환, §8 A/B 실측). `embeddings.sqlite` 부재 시 BM25 단독 자동 강등 · 끄려면 `DART_HYBRID=0` | 위로 닿지 않는 서술형 |

### 핵심 설계

| # | 원칙 | 구현 |
|---|---|---|
| D1 | **Numeric Determinism** — 수치는 LLM이 생성하지 않는다 | `compute`의 operand는 `fact_id` 참조. 숫자 리터럴 입력 경로가 없다 |
| D2 | **Address before Search** | 법정 목차가 70개사 동일 → 주소로 직접 조회 후 검색 폴백 |
| D3 | **Correction-First** | 정정 체인 해소 후 `is_effective=1`만 집계 (거래소공시 정정률 43%) |
| D4 | **Evidence as Product** | `retrieved_context`·`think_trace`는 로그가 아니라 채점 산출물 |
| D5 | **Abstain over Guess** | 근거 미달 시 답을 만들지 않는다 (10종 사유) |
| D6 | **Platform-Native** | 임베딩·리랭킹까지 CLOVA API로 통일 — "HyperCLOVA X만" 제약 안전판 |

---

## 4. 이 코퍼스의 함정 7가지 (전부 실측 확인)

| # | 함정 | 대응 |
|---|---|---|
| 1 | 거래소공시 1,469건이 `.xml`이지만 **HTML**, meta는 `euc-kr` 선언인데 **바이트는 UTF-8** | `parsers/exchange.py` — 바이트를 UTF-8로 강제 디코딩. 회귀 테스트 존재 |
| 2 | 거래소공시 **정정률 43%** | 정정 체인 해소 → 유효본만 집계 |
| 3 | 질의는 **통용명**("현대차"), 조인 키는 **법인명**("현대자동차") | 별칭 4-way + 수기 24종 |
| 4 | XBRL 태깅률 불균일 (annual 60% / half·quarter 28%) | Stage C 표 폴백 (`III-1 요약재무정보` 앵커) |
| 5 | 금액 단위 **원/천원/백만원 혼용**, 표본 27% 미표기 | `value_krw` 원 단위 정규화 + `unit_confidence=low`는 비교 배제 |
| 6 | 공시에 임원 **생년월일·성별** 존재 | PII 게이트 + 섹션 마스킹 |
| 7 | 반기·분기 XBRL은 **누적(`dHYA`)과 당기 3개월(`dHYQ`)이 공존** | `period_scope` 분리 저장 — 섞으면 오답 |

> #7 실측: 삼성전자 2025 반기 매출 — 누적 153.7조 / 당기 74.6조.
> 구분 없이 조회하면 둘 중 아무거나 나온다.

---

## 5. 키가 없어도 동작한다

`CLOVA_API_KEY` 미설정 시 `StubProvider`로 폴백하고 **결정론 경로만으로 답변한다.**
수치·집계·비교·이벤트 추적은 전부 SQL이므로 LLM 없이도 정답이 나온다.

| 계층 | 키 필요 | 키 없을 때 |
|---|:---:|---|
| 파서 · Fact Store · 정정 체인 · 별칭 | ❌ | 정상 |
| BM25 검색 · 섹션 주소 조회 | ❌ | 정상 |
| 도구 6종 · 검증기 V1~V5 · Abstention | ❌ | 정상 |
| 벡터 검색 (`DART_HYBRID=1` 파일럿) | ✅ | 비활성 (BM25 단독). 기본값도 OFF |
| Reranker | ✅ | 비활성 — **미배선** (구현만 존재) |
| LLM 서술 생성 | ✅ | 템플릿 조립으로 대체 |

`/ready`의 `notes`에 현재 강등 상태가 표시된다.

---

## 6. 운영 주의

### CLOVA rate limit — 병목은 요청 수가 아니라 토큰이다

응답 헤더 실측 (2026-08-18):

```
x-ratelimit-limit-requests : 60      x-ratelimit-limit-tokens : 60000
x-ratelimit-reset-*        : 13s
```

부하 시 **요청은 남는데 토큰이 먼저 바닥난다** — `토큰 101 · 요청 53`이 실제 관측값이다.
HCX-007이 thinking 모델이라 추론 토큰이 예산을 먹기 때문이다.

🔴 **재시도가 없으면 실패가 실패를 부른다.** 429는 즉시 반환되므로 다음 호출이 0.14초 뒤에
또 두드리고, 리셋될 틈이 없다. 순차 실행(분당 12회, 한도의 1/5)에서도 **60건이 폐기**됐다.
`llm/ratelimit.py`가 두 겹으로 막는다 — 헤더가 알려준 시간만큼 자고 재시도(반응) +
남은 토큰이 바닥나기 전 선제 대기(예방).

**동시 요청 실측** (`python3 eval/load_test.py --concurrency N --n N`, 타임아웃 300초):

| 동시 | 성공 | 처리량 | 지연 중앙 | 최대 | 타임아웃 대비 |
|---:|---:|---:|---:|---:|---:|
| 5 | 20/20 | 7.0/min | 32s | 90s | 30% |
| 10 | 20/20 | 8.9/min | 60s | 81s | 27% |
| 30 | 30/30 | 15.8/min | 74s | 114s | 38% |
| 50 | 50/50 | 18.4/min | 89s | **163s** | **54%** |

동시 50에서도 전원 성공하고 5xx는 0이다. 누적 429 324건 중 267건을 재시도가 회수했고,
**소진된 52건은 결정론 템플릿으로 강등**됐다 — 답변이 사라지는 게 아니라 문장만 담백해진다.

### 🔴 그래서 "아는 것은 규칙으로 내린다"

목차 주소 라우팅도 같은 LLM 경로다. 부하로 라우팅이 죽으면 검색으로 떨어져 섹션 인용이
틀어진다. 그래서 **실패가 확인된 유형은 `INTENT_PATHS` 규칙으로 내리고**(배당 → III-6,
`사업의 개요` 조사 허용), LLM 라우팅은 **아직 모르는 롱테일에만** 쓴다.
LLM을 완전히 끈 상태에서도 골드셋이 통과하는지가 이 정책의 검증 기준이다 (§8).

### 검색 색인은 디스크(FTS5)에 둔다 — 메모리 제약이 하드 제약이다

초기 구현은 BM25를 메모리에 상주시켰다. 실측 결과 **NCP 권장 서버(2vCPU/4GB)에 올라가지 않는다.**

| 6,504 섹션 (동일 조건) | 인메모리 BM25 | **FTS5 (현행)** |
|---|---|---|
| 색인 상주 메모리 | +194 MB | **+6 MB** |
| 로드 순간 피크 | 561 MB | 없음 (오픈 21 ms) |
| 질의 지연 | 2.9 ms | 19.6 ms |

**전체 코퍼스 112,797 섹션 실측** (외삽 아님):

| | 값 |
|---|---|
| 서버 기동 | **0.7 ~ 2.4 초** |
| 서버 상주 RSS | **44 ~ 396 MB** — **4 GB의 1 ~ 10%** |
| 질의 지연 | 워밍 후 1 ~ 26 ms (콜드 최대 1.5 s) |
| DB (색인 포함) | 3,871 MB |

같은 조건에서 인메모리였다면 상주 3.4 GB / **로드 피크 9.7 GB**(17.3배)로, 4 GB 서버는
기동 중에 죽는다. FTS5는 색인을 `dart.sqlite` 안에 두고 조회 시점에만 페이지를 읽으므로
**상주 메모리가 코퍼스 크기와 무관**하다.

> ⚠️ 콜드 캐시 첫 접근이 최대 1.5 초다(3회차엔 26 ms). 평가 직전 대표 질의 몇 개로
> 워밍업하면 이 지연이 사라진다.

품질 손실은 크지 않다: 인메모리 BM25 대비 **top-5 중복 92%**, top-1은 8질의 중 7건 동일
(FTS5 `bm25()`는 k1=1.2·b=0.75 고정이라 미세하게 다르다 — `bm25_k1`/`bm25_b` 설정은 무시된다).

**한국어**: FTS5에 Kiwi를 직접 붙일 수 없어(커스텀 토크나이저는 C API 필요), 색인 시점에
Kiwi로 분해한 토큰을 공백으로 이어 붙여 저장하고 `unicode61`로 색인한다. 질의도 같은 분해를
거치므로 형태소 매칭이 유지된다.

`scripts/build_index.py`가 FTS5 색인을 자동 생성하므로 **배포 전 반드시 1회 실행**할 것.
토크나이저가 바뀌면(kiwi ↔ n-gram) `index_meta`로 감지해 재빌드를 유도한다 — 색인 토큰이
호환되지 않기 때문. 레거시 인메모리 경로는 `--also-pickle`로 남겨두었다(회귀 비교용).

---

## 7. 구조

```
src/dart_agent/
├── models.py numbers.py metrics.py config.py   공유 계약 (단위 정규화 · 지표 사전)
├── parsers/     periodic · exchange · holding · major
├── store/       schema.sql · db · repository · alias · corrections
├── retrieval/   tokenizer(Kiwi) · fts_index(FTS5 디스크 색인) · bm25(RRF·레거시) · section_map
├── agent/       tools(6종) · verifier(V1~V5) · abstention(10종) · pii
│             narrate(서술) · route_section(목차 라우팅) · tabular(표→서술) · orchestrator
├── llm/         provider · clova(OpenAI 호환) · stub
└── api/server.py
scripts/build_index.py · run_server.py
specs/SPEC.md · specs/TASKS.md          실행 가능 명세 + 태스크 DAG
proposal/                               기술명세서 · MVP 제안서 · 조사 근거
eval/          goldset.jsonl(186) · baseline/v1.0.0(동결) · score.py · retrieval_ab.py
tests/                                  428 tests (단위·계약·회귀)
```

---

## 8. 현재 상태

| 항목 | 상태 |
|---|---|
| **골드셋** | **186/186 = 100%** (`eval/goldset.jsonl`, **10유형** 전부 만점, 2026-09-03 배포 직전 실측) |
| **릴리즈 게이트** | `./scripts/gate.sh` — 동결 baseline manifest + quality·safety·regression·latency·cost |
| 테스트 | **428건 통과** (`python3 -m pytest`, 전체 색인 기준) |
| **인덱스** | **전체 4,204건 · 파싱 성공률 100%** — 섹션 112,797 · 재무 사실 2,021,894 · 이벤트 3,150 |
| 정답 대조 | 삼성전자 300.9조 / SK하이닉스 영업이익 23.5조 / 기아 107.4조 / POSCO홀딩스 72.7조 |
| API 계약 | 실 HTTP 전수 200 · 5필드 · abstention **10종** · **경계 10종(빈입력·2,400자·특수문자·중복 id 등) 배포 서버 실측 계약 유지** |
| 메모리 | 서버 상주 **44~396 MB = 4 GB의 1~10%** · 기동 0.7~2.4초 |
| 색인 정합 | FTS5 112,797 = section 112,797 = is_effective 112,797 (누락 0) |
| HCX 활용 | 골드셋 1회당 **약 200 호출** (서술 + 목차 라우팅) · **문항당 1,195 토큰** |
| 동시 부하 | 동시 50 전원 성공 · 최대 163초 (타임아웃의 54%) · 5xx 0 |

### 릴리즈 게이트 — AITHOR `eval-audit` 4축 + 회귀 (2026-08-25 실측, 하이브리드 ON)

```
quality 186/186 · safety 0건 · regression 0건 · p95 23.3s · cost 1,280 토큰/문항
```

| 축 | 실측 | 기준 | 무엇을 막는가 |
|---|---:|---:|---|
| quality | 186/186 | 기준선 186 | 정확도 하락 |
| safety | 0건 | 0건 | must-not-do·경계 계약 위반 |
| **regression** | 0건 | 0건 | **과거 사고 재발** (아래) |
| latency | p95 23.3s · max 32.9s | < 240s | 평가 타임아웃(300초) 초과 |
| **cost** | 1,280 토큰/문항 | < 4,000 | 크레딧 소진 |

> 하이브리드 전환 전(BM25 단독) 대비: 정확도 동일(186/186), p95 27.4→23.3s,
> 토큰 1,195→1,280(+7%, 질의 임베딩 1회 추가분). 임베딩은 chat과 **레이트리밋 풀이
> 별개**라 답변 생성 예산을 잠식하지 않는다(아래 실측).

🔴 **regression 축이 값이 아니라 시간을 본다.** 2026-08-19에 한 문항이 **578초**가 걸린
사고가 있었는데 **정확도는 100%였다** — 값만 보는 채점기는 이 재발을 영원히 못 본다.
`REG-001`이 그 문항에 `max_latency_ms=60000`을 걸어 시간 축 회귀를 잡는다.

> 리포트(`eval/v13.json`)는 `.gitignore` 대상이다 — 실행마다 재생성되는 산출물이지
> 소스가 아니다. 재현하려면 서버를 띄우고 `./scripts/gate.sh`.

### 골드셋 밖 질의 — 주최측 참고 질의로 3종 보강 (2026-09-03)

골드셋 186문항은 만점이지만 **그 밖의 질의 유형에서 결함이 나왔다.** 주최측 참고 질의
세트로 두들겨 찾은 3건을 고쳤다 — 골드셋이 100%라는 사실이 커버리지를 증명하지 않는다.

| 질의 | 고치기 전 | 고친 뒤 |
|---|---|---|
| "삼성전자 실적 요약해줘" | 표 원문 400자 덤프 | `매출액 133,873,444, 영업이익 57,232,797 …` 서술 (`agent/tabular.py`) |
| "현대차와 기아 중 어디가 더 성장했어?" | 기아만 답변 | 지표 미특정 역질문 (`no_comparison_metric`) |
| "부채비율이 위험한 수준이야?" | 표 덤프 | 투자 판단이라 기권 + 확인 가능한 사실 제시 (`forbidden_judgment`) |

🔴 **표 파서를 줄 단위로 쓰면 0건이 나온다.** DB `section.text`에는 개행이 **하나도 없다**
(실측: 2,750자 / 개행 0). 라벨 인접 스캔으로 바꿨고, 개행 없는 표를 테스트 픽스처에 박아
줄 기반 재작성이 조용히 되살아나지 못하게 했다.

🔴 **같은 회차에 숨어 있던 버그 하나가 드러났다.** 근거(`retrieved_context`)는 섹션 본문을
1,200자까지 실었는데 답변 추출은 전문을 보고 있었다. 답변의 수치가 근거에 없으니 V1이
"근거 없는 수치"로 잡았고, 검증 재시도가 **그 문장을 통째로 지웠다** — "삼성전자 실적 요약"의
답변이 헤더 한 줄만 남은 것이 그것이다. `SECTION_EVIDENCE_CHARS=2400`으로 양쪽을 같은
범위에 묶었다 (12개사 실측에서 2,400자면 12/12 손익계산서가 들어온다).

### 🔴 LLM이 죽어도 정확도는 안 떨어진다 — 실측

| 실행 | HCX | 골드셋 | 지연 중앙 |
|---|:---:|---:|---:|
| 정상 | 약 200 호출 | **186/186 (100%)** | 10.5s |
| **LLM 완전 차단** | 0 호출 | **177/177 (100%)** | **0.003s** |

> ⚠️ LLM 차단 실측은 골드셋 177문항 시점의 것이다. 경계·회귀 9문항을 더한 뒤로는
> 재측정하지 않았다 — 재현하려면 `.env`에서 `CLOVA_API_KEY`를 빼고 재기동한다.

같은 만점이다. LLM은 **문장을 다듬고 목차 주소를 고를 뿐**, 수치·집계·비교·기권은 전부
SQL과 규칙이 확정하기 때문이다 (설계 D1). 429·장애·키 만료 어느 쪽이 와도 답변 품질의
바닥이 보장된다 — 잃는 것은 문장의 자연스러움이지 정답이 아니다.

**미완**: Reranker (구현만, 미배선).

### 하이브리드 검색 — A/B 실측 (2026-08-25)

검색 계층만 직접 측정했다 (`eval/retrieval_ab.py`). 서버 E2E가 아니라 **retrieval
레벨**인 이유는, section 질문이 운영 경로에서 `get_section`(결정론 주소 조회)으로 답해
`doc_search`의 개선이 관측되지 않기 때문이다. `doc_search`는 그 주소 추론이 실패했을 때의
폴백이며, **미지의 평가 질문에서는 이 폴백이 곧 답변 품질**이다.

**두 세트로 쟀다** — 골든셋 25문항(파라미터를 고를 때 쓴 튜닝셋)과 홀드아웃 30문항
(`eval/holdout_section.jsonl`, 튜닝에 쓰이지 않은 30개 기업). 판단 기준은 홀드아웃이다.

| 검색 팔 | 골든셋 MRR | 홀드아웃 MRR |
|---|---:|---:|
| BM25 단독 (기존 운영값) | 0.290 | 0.117 |
| 벡터 단독 (bge-m3) | 0.502 | 0.429 |
| RRF k=60 w=1 (교과서 기본값) | 0.419 | 0.258 |
| RRF k=10 w=2 | 0.620 | 0.413 |
| **RRF k=5 w=3 (채택)** | 0.538 | **0.436** |

🔴 **교과서 기본값 k=60이 모든 가중치 조합에서 최하위였다.** k가 크면 상위 랭크의 우위가
평탄해져, 확연히 우수한 팔(벡터)을 열등한 팔이 끌어내린다.

🔴 **골든셋 최고값(k=10 w=2)을 쓰지 않았다.** 그 25문항으로 고른 값이라 낙관 편향이 있고,
실제로 홀드아웃에서 우위가 사라졌다(0.413 < 벡터 단독 0.429). 튜닝셋 성적은 판단 근거가
못 된다는 것을 이 프로젝트에서 직접 확인한 셈이다.

⚠️ **한계**: 채택값도 홀드아웃을 *보고* 골랐으므로 그 세트도 이제 오염됐다. 상위 조합들의
홀드아웃 차이(0.41~0.44)는 30문항에서 노이즈 범위다 — **견고한 결론은 "k=60을 쓰지 마라"이지
특정 조합의 우월성이 아니다.** 벡터 커버리지도 부분이다(최신 사업보고서 10,123섹션).

**운영 기본 ON** (2026-08-25 전환). 하이브리드를 켠 상태로 **골든셋 186/186 무회귀**를
확인했고(10유형 전부 100%, 지연 중앙값 10.3초 — 이전과 동일), 벡터 스토어나 CLOVA 키가
없으면 자동으로 BM25 단독으로 강등된다. 끄려면 `DART_HYBRID=0`. 실제 활성 여부는
`/ready`의 `notes`가 말한다.

#### 레이트리밋 — chat과 embedding은 **별도 풀** (실측 2026-08-24)

하이브리드는 질의당 임베딩 호출이 1회 추가된다. 그것이 답변 생성 예산을 잠식하는지가
운영상 관건인데, **잠식하지 않는다**. 임베딩 배치가 자기 풀의 56/60을 쓰는 동안
같은 시점 chat 풀은 59/60이 남아 있었다 (응답 헤더 `x-ratelimit-remaining-requests`):

| 엔드포인트 | limit | 배치 가동 중 remaining |
|---|---:|---:|
| `/chat/completions` | 60/min | **59** |
| `/embeddings` | 60/min · 40K tok/min | 4 |

동시 질의가 60/min을 넘으면 질의 임베딩만 429가 나고 `doc_search`는 BM25 단독으로
강등된다 — 답변이 끊기지 않는다 (`agent/tools.py`의 except 분기).
