# dis-029 · 공시 Agent (Disclosure Analyst)

제10회 2026 미래에셋증권 AI Festival — **별빛하늘솜사탕**

> **설계 한 줄**: 공시의 **숫자는 조회하고 문장은 생성한다.**
> DART XML의 XBRL 인라인 태깅·정형 필드에서 사실을 먼저 확정하고, LLM은 해석과 서술만 맡는다.
> 그래서 환각을 *탐지*하는 대신 **발생 불가**로 만든다.

---

## 0. 평가용 API End-point

<!-- 🔴 제출 필수. 배포 완료 후 <공인IP>를 실제 값으로 교체할 것. -->

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

```bash
curl -G "http://<공인IP>/answer" \
  --data-urlencode "question_id=Q-001" \
  --data-urlencode "question=삼성전자의 2024년 연결기준 매출액은?"
```

### 재현 환경 · 전처리 산출물

| 항목 | 위치 |
|---|---|
| 의존성 (버전 고정) | `requirements.txt` |
| 컨테이너 (선택) | `Dockerfile` |
| 인프라 (선택) | `infra/` — Terraform |
| **전처리 산출물** | `index/dart.sqlite` (3.9GB) — <!-- 🔴 클라우드 스토리지 링크 기입 --> |

> 코퍼스 원본(5.2GB)과 인덱스(3.9GB)는 저장소 용량 제약으로 제외했다.
> 인덱스는 `scripts/build_index.py`로 **완전 재생성 가능**하며(§4), 별도 링크로도 제출한다.

---

## 1. 빠른 시작

```bash
# 0) 의존성
python3 -m pip install -r requirements.txt

# 1) 인덱스 빌드 — 코퍼스를 docs/3.공시/corpus/ 에 두고 실행
python3 scripts/build_index.py --limit 60 --rebuild   # 스모크 (약 1분)
python3 scripts/build_index.py --rebuild              # 전량 4,204건 (약 32분)

# 2) 서버 (로컬 개발)
python3 run_server.py            # http://0.0.0.0:8000
#    배포 시 외부는 표준 포트 80 → docker run -p 80:8000

# 3) 테스트
python3 -m pytest                # 157 passed

# 4) 자체 평가
python3 eval/score.py            # 골드셋 177문항
```

### 환경변수

| 변수 | 기본값 | 설명 |
|---|---|---|
| `CLOVA_API_KEY` | (없음) | HyperCLOVA X 키. **없으면 StubProvider로 폴백**하고 서버는 계속 동작 |
| `CLOVA_BASE_URL` | `https://clovastudio.stream.ntruss.com/v1/openai` | OpenAI 호환 엔드포인트 |
| `CLOVA_CHAT_MODEL` | `HCX-007` | 128K ctx · function calling · structured output · thinking |
| `CLOVA_EMBEDDING_MODEL` | `bge-m3` | 8,192 tokens / 1,024 dim |
| `DART_CORPUS_ROOT` | `docs/3.공시/corpus` | 코퍼스 경로 (인덱싱 시에만 필요) |
| `DART_DB_PATH` | `index/dart.sqlite` | Fact Store + FTS5 색인 |
| `DART_SEARCH_THRESHOLD` | `0.35` | 기권 판정 임계값 |
| `PORT` / `HOST` | `8000` / `0.0.0.0` | |

---

## 2. API 응답 규격

```json
{
  "question_id": "Q-001",
  "question": "삼성전자의 2024년 연결기준 매출액은 얼마인가?",
  "retrieved_context": "[C1] 삼성전자 사업보고서 (2024.12) · 접수번호 20250311001085 · III-2-2\n      매출액 (주29) 300,870,903 백만원 (연결, FY, xbrl)",
  "think_trace": "[1] 질의 해석 … [2] 계획 … [3] 도구 실행 … [4] 검증 … [5] 결론",
  "answer": "삼성전자의 2024년 연결기준 매출액은 300,870,903백만원(300.9조원)입니다 [C1].",

  "citations": [{"id":"C1","doc_id":"...","rcept_no":"...","section":"III-2-2","source":"xbrl"}],
  "confidence": "high", "abstained": false,
  "abstain_reason": null, "verification": "검증 통과", "latency_ms": 3
}
```

명시 5필드는 **항상 존재하며 전부 문자열**이다. 그 아래는 부가 필드다.

🔴 **HTTP 500을 반환하지 않는다.** 모든 내부 예외를 기권 응답 + 200으로 변환한다 —
평가 중 한 문항의 예외가 서버 장애로 번지면 나머지 문항까지 잃기 때문. 빈 질의만 400이며
그 경우에도 계약 필드는 채워서 보낸다.

부가 엔드포인트(운영용, 평가 대상 아님): `/health` · `/ready` · `/meta`

---

## 3. 아키텍처

```
GET /answer
   │
   ├─ ① Guard        입력 살균 · PII 질의 차단
   ├─ ② Q-이해       기업 별칭(4-way) · 섹터 · 기간 · 누적/당기 · 연결/별도 · 지표 분해
   ├─ ③ 도구 라우팅   주소 지정 → 사실 조회 → 검색  (이 순서가 설계 D2)
   │     fact_query · get_section · doc_search · event_query · trace_chain · compute
   ├─ ④ 근거 조립     정정 체인 해소 → 유효본만 · citation id 부여
   ├─ ⑤ 답변 조립     수치는 fact 값 그대로 (D1)
   └─ ⑥ 검증 V1~V5   전부 결정론 — LLM 호출 0건
```

### 3계층 데이터

| 계층 | 내용 | 담당 질의 |
|---|---|---|
| **L1 Fact Store** (SQLite) | XBRL 재무 사실 202만 · 이벤트 3,150 · 정정 체인 · 별칭 232 | 수치·집계·비교·순위 |
| **L2 Section Store** | 법정 목차 주소(`II-3`, `III-2-2` …) 112,797 | 문서·섹션 특정 조회 |
| **L3 검색** | BM25(Kiwi 형태소, **SQLite FTS5 디스크 색인**) + RRF(k=60) [+ bge-m3 벡터] | 위로 닿지 않는 서술형 |

### 핵심 설계

| # | 원칙 | 구현 |
|---|---|---|
| D1 | **Numeric Determinism** — 수치는 LLM이 생성하지 않는다 | `compute`의 operand는 `FactHit` 참조. 숫자 리터럴 입력 경로가 **없다** |
| D2 | **Address before Search** | 법정 목차가 70개사 동일 → 주소로 직접 조회 후 검색 폴백 |
| D3 | **Correction-First** | 정정 체인 해소 후 `is_effective=1`만 집계 (거래소공시 정정 2,515건) |
| D4 | **Evidence as Product** | `retrieved_context`·`think_trace`는 로그가 아니라 채점 산출물 |
| D5 | **Abstain over Guess** | 근거 미달 시 답을 만들지 않는다 (9종 사유) |
| D6 | **Platform-Native** | 임베딩·리랭킹까지 CLOVA API로 통일 — "HyperCLOVA X만" 제약 안전판 |

---

## 4. 이 코퍼스의 함정 7가지 (전부 실측 확인)

| # | 함정 | 대응 |
|---|---|---|
| 1 | 거래소공시 1,469건이 `.xml`이지만 **HTML**, meta는 `euc-kr` 선언인데 **바이트는 UTF-8** | 선언 무시하고 UTF-8 강제 디코딩. 회귀 테스트 존재 |
| 2 | 거래소공시 **정정 2,515건** | 정정 체인 해소 → 유효본만 집계 |
| 3 | 질의는 **통용명**("현대차"), 조인 키는 **법인명**("현대자동차") | 별칭 4-way + 수기 16종, 미연결 0 |
| 4 | XBRL 태깅률 불균일 (annual 60% / half·quarter 28%) | 표 파싱 폴백 (`III-1 요약재무정보` 앵커) |
| 5 | 금액 단위 **원/천원/백만원 혼용**, 표본 27% 미표기 | `value_krw` 원 단위 정규화 + `unit_confidence=low`는 비교 배제 |
| 6 | 공시에 임원 **생년월일·성별** 존재 | PII 게이트 + 섹션 마스킹 (회사 단위 질의는 계속 답변) |
| 7 | 반기·분기 XBRL은 **누적(`dHYA`)과 당기 3개월(`dHYQ`)이 공존** | `period_scope` 분리 저장 + 질의 파싱(`parse_scope`) |

> **#7 실측**: 삼성전자 2025 반기 매출 — 누적 153.7조 / 당기 74.6조.
> 구분하지 않으면 둘 중 아무 값이나 나오고, 절반이 오답이 된다.

> **중복 해소 실측**: 삼성전자 2024 연결 매출 후보가 **16개**다 —
> 본표 300.9조 · 부문합계 329.4조 · 사업부문별 174.9조 · **연결조정 −28.5조**.
> 본표(III-2/III-4) 우선 규칙이 없으면 주석의 부문별 수치가 답으로 나간다.

---

## 5. 키가 없어도 동작한다

`CLOVA_API_KEY` 미설정 시 `StubProvider`로 폴백하고 **결정론 경로만으로 답변한다.**

| 계층 | 키 필요 | 키 없을 때 |
|---|:---:|---|
| 파서 · Fact Store · 정정 체인 · 별칭 | ❌ | 정상 |
| FTS5 검색 · 섹션 주소 조회 | ❌ | 정상 |
| 도구 6종 · 검증기 V1~V5 · 기권 | ❌ | 정상 |
| 벡터 검색 · 리랭킹 | ✅ | 비활성 (BM25 단독) |
| LLM 서술 생성 | ✅ | 템플릿 조립으로 대체 |

`/ready`의 `notes`에 현재 강등 상태가 표시된다.

---

## 6. 운영 주의

### 검색 색인은 디스크(FTS5)에 둔다 — 메모리가 하드 제약

초기 구현은 BM25를 메모리에 상주시켰고, **NCP 권장 서버(2vCPU/4GB)에 올라가지 않았다.**

| 6,504섹션 실측 | 인메모리 BM25 | **FTS5** |
|---|---|---|
| 색인 상주 메모리 | +194 MB | **+6 MB** |
| 로드 순간 피크 | 561 MB | 없음 (오픈 21 ms) |
| 질의 지연 | 2.9 ms | 19.6 ms |

**전체 112,797섹션 실측**: 기동 0.7–2.4초 · 서버 상주 **44–396 MB = 4GB의 1~10%** ·
질의 워밍 후 1–26 ms · DB 3,871 MB. 상주 메모리가 **코퍼스 크기와 무관**하다.

> ⚠️ 콜드 캐시 첫 접근이 최대 1.5초다(3회차엔 26 ms). 평가 시작 전 워밍업 권장.

**한국어**: FTS5에 Kiwi를 직접 붙일 수 없어(커스텀 토크나이저는 C API 필요),
색인 시점에 Kiwi로 분해한 토큰을 공백으로 이어 붙여 `unicode61`로 색인한다.
질의도 같은 분해를 거치므로 형태소 매칭이 유지된다.

### 배포 (포트 80)

```bash
docker build -t dart-agent .
docker run -d --name dart-agent --restart always \
  -p 80:8000 -v /data:/data -e CLOVA_API_KEY=nv-... dart-agent
```

앱은 컨테이너 내부에서 8000을 듣고 매핑으로 80을 연다 —
컨테이너 안에서 80을 직접 바인딩하면 root 권한이 필요하기 때문.

---

## 7. 구조

```
src/dart_agent/
├── models.py numbers.py metrics.py config.py   공유 계약 (단위 정규화 · 지표 사전)
├── parsers/     periodic · exchange · holding · major
├── store/       schema.sql · db · repository · alias · corrections
├── retrieval/   tokenizer(Kiwi) · fts_index(FTS5) · bm25(RRF) · section_map
├── agent/       tools(6종) · verifier(V1~V5) · abstention · pii · orchestrator
├── llm/         provider · clova(OpenAI 호환) · stub
└── api/server.py
scripts/build_index.py · run_server.py
eval/         골드셋 177문항 + 자동 채점 하네스
specs/        실행 가능 명세 + 태스크 DAG
proposal/     기술명세서 · MVP 제안서 · 조사 근거
infra/        Terraform (NCP)
tests/        157 tests
GUIDE.html    구축 가이드 (12절)
```

---

## 8. 현재 상태

| 항목 | 상태 |
|---|---|
| 테스트 | **157/157 통과** (`python3 -m pytest`) |
| 인덱스 | **전체 4,204건 · 파싱 성공률 100%** — 섹션 112,797 · 재무 사실 2,021,894 |
| **자체 평가** | **골드셋 177문항 · 97.2%** (`eval/score.py`) |
| 정답 대조 | 삼성전자 300.9조 / SK하이닉스 영업이익 23.5조 / 기아 107.4조 / POSCO홀딩스 72.7조 |
| API 계약 | 실 HTTP 전수 200 · 5필드 · 기권 9종 정상 |
| 메모리 | 서버 상주 44~396 MB = 4GB의 1~10% |

### 자체 평가 상세 (`eval/`)

정답을 지어내지 않는다 — **Fact Store에서 사실을 꺼내 그 사실을 묻는 문항을 역생성**하고,
정답은 도구가 아니라 **raw SQL**로 독립 산출한다. 177문항 전부 자동 채점된다.

| 유형 | 정확도 |
|---|---|
| 단일 수치 / 연결·별도 / 비교 / 증감 / **기권 트랩** | **100%** |
| 누적·당기 구분 | **100%** |
| 이벤트 | 95.5% |
| 섹션 주소 | 84.0% |

> 골드셋이 만들어지자마자 결함 2건을 잡았다 — `fact_query`에 기간 필터가 없어
> "상반기 매출"에 **연간 값**이 반환되던 것(0/20 → 20/20), 그리고
> "지금 사야 할까요?"라는 **투자 권유에 답하던 것**(14/15 → 15/15).

**미완 (키 필요)**: HCX Planner/Synthesizer 실호출 · 벡터 인덱스 · Reranker.
