# 공시 Agent (Disclosure Analyst)

제10회 2026 미래에셋증권 AI Festival — 공시 데이터 기반 자연어 질의응답 Agent

> **설계 한 줄**: 공시의 **숫자는 조회하고 문장은 생성한다.**
> DART XML의 XBRL 인라인 태깅·정형 필드에서 사실을 먼저 확정하고, LLM은 해석과 서술만 맡는다.
> 그래서 환각을 *탐지*하는 대신 **발생 불가**로 만든다.

---

## 0. 평가용 API End-point

<!-- 🔴 제출 필수 항목. 배포 완료 후 아래 <공인IP>를 실제 값으로 교체할 것. -->

```
http://<공인IP>/answer
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
curl -G "http://<공인IP>/answer" \
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
```

### 환경변수

| 변수 | 기본값 | 설명 |
|---|---|---|
| `CLOVA_API_KEY` | (없음) | HyperCLOVA X 키. **없으면 StubProvider로 폴백**하고 서버는 계속 동작 |
| `CLOVA_BASE_URL` | `https://clovastudio.stream.ntruss.com/v1/openai` | OpenAI 호환 엔드포인트 |
| `CLOVA_CHAT_MODEL` | `HCX-007` | 128K ctx · function calling · structured output · thinking |
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
  "confidence": "high", "abstained": false, "verification": "검증 통과", "latency_ms": 9
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
| **L3 검색** | BM25(Kiwi 형태소, **SQLite FTS5 디스크 색인**) + RRF(k=60) [+ bge-m3 벡터, 키 필요] | 위로 닿지 않는 서술형 |

### 핵심 설계

| # | 원칙 | 구현 |
|---|---|---|
| D1 | **Numeric Determinism** — 수치는 LLM이 생성하지 않는다 | `compute`의 operand는 `fact_id` 참조. 숫자 리터럴 입력 경로가 없다 |
| D2 | **Address before Search** | 법정 목차가 70개사 동일 → 주소로 직접 조회 후 검색 폴백 |
| D3 | **Correction-First** | 정정 체인 해소 후 `is_effective=1`만 집계 (거래소공시 정정률 43%) |
| D4 | **Evidence as Product** | `retrieved_context`·`think_trace`는 로그가 아니라 채점 산출물 |
| D5 | **Abstain over Guess** | 근거 미달 시 답을 만들지 않는다 (7종 사유) |
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
| 벡터 검색 · Reranker | ✅ | 비활성 (BM25 단독) |
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
├── agent/       tools(6종) · verifier(V1~V5) · abstention · pii · orchestrator
├── llm/         provider · clova(OpenAI 호환) · stub
└── api/server.py
scripts/build_index.py · run_server.py
specs/SPEC.md · specs/TASKS.md          실행 가능 명세 + 태스크 DAG
proposal/                               기술명세서 · MVP 제안서 · 조사 근거
tests/                                  302 tests
```

---

## 8. 현재 상태

| 항목 | 상태 |
|---|---|
| **골드셋** | **177/177 = 100%** (`eval/goldset.jsonl`, 8유형 전부 만점) |
| 테스트 | **302건 통과** (`python3 -m pytest`, 전체 색인 기준) |
| **인덱스** | **전체 4,204건 · 파싱 성공률 100%** — 섹션 112,797 · 재무 사실 2,021,894 · 이벤트 3,150 |
| 정답 대조 | 삼성전자 300.9조 / SK하이닉스 영업이익 23.5조 / 기아 107.4조 / POSCO홀딩스 72.7조 |
| API 계약 | 실 HTTP 10문항 전수 200 · 5필드 · abstention 7종 정상 |
| 메모리 | 서버 상주 **44~396 MB = 4 GB의 1~10%** · 기동 0.7~2.4초 |
| 색인 정합 | FTS5 112,797 = section 112,797 = is_effective 112,797 (누락 0) |
| HCX 활용 | 골드셋 1회당 **199 호출** (서술 + 목차 라우팅) · 429 폐기 0 |
| 동시 부하 | 동시 50 전원 성공 · 최대 163초 (타임아웃의 54%) · 5xx 0 |

### 🔴 LLM이 죽어도 정확도는 안 떨어진다 — 실측

| 실행 | HCX | 골드셋 | 지연 중앙 |
|---|:---:|---:|---:|
| 정상 | 199 호출 | **177/177 (100%)** | 10.4s |
| **LLM 완전 차단** | 0 호출 | **177/177 (100%)** | **0.00s** |

같은 만점이다. LLM은 **문장을 다듬고 목차 주소를 고를 뿐**, 수치·집계·비교·기권은 전부
SQL과 규칙이 확정하기 때문이다 (설계 D1). 429·장애·키 만료 어느 쪽이 와도 답변 품질의
바닥이 보장된다 — 잃는 것은 문장의 자연스러움이지 정답이 아니다.

**미완**: 벡터 인덱스 · Reranker (BM25/FTS5 단독으로 동작 중).
