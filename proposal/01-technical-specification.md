# 공시 Agent — 기술 명세서

> 제10회 2026 미래에셋증권 AI Festival · 주제 「공시 Agent (Disclosure Analyst)」
> v0.1 · 2026-07-30 · 근거 문서: [`00-research-findings.md`](./00-research-findings.md)

---

## 0. 설계 요지 (1페이지 요약)

### 0-1. 문제의 재정의

과제는 표면적으로 "공시 RAG"지만, 참고용 질의 6종을 역산하면 **4종이 단일 문서 검색으로 풀리지 않는다**
(연구결과 §1-4). 실패는 검색 실패가 아니라 **① 숫자 오독 ② 근거 누락 ③ 문서 간 연결 실패**에서 발생한다.

동시에 코퍼스 실측은 이 문제가 **일반 RAG 문제가 아님**을 보여준다:

- 재무 수치는 **XBRL 인라인 태깅**되어 있다 (`<TE ACODE="dart_OperatingIncomeLoss" ACONTEXT="CFY2024dFY_...ConsolidatedMember">`)
- 계약·지분·자금조달 공시는 **완전 정형 key-value**다
- 정기공시는 **70개사 동일 법정 목차**를 갖는다
- 정정공시는 **원본 포인터 + 정정전/후 diff 표**를 스스로 담는다

### 0-2. 핵심 설계 원칙

> **숫자는 계산하고, 문장은 생성한다. 둘을 섞지 않는다.**

| # | 원칙 | 구현 귀결 |
|---|---|---|
| **D1** | **Numeric Determinism** — 수치는 LLM 출력이 아니라 **DB 조회 결과의 슬롯 주입**이다 | Fact Store + slot binding + 사후 검증기 |
| **D2** | **Address before Search** — 법정 목차 주소로 먼저 조회하고, 실패 시 검색한다 | Section Store 주소 지정 → Hybrid 검색 순서 |
| | ↳ *외부 검증*: PageIndex(Vectorless RAG, 25.4k★)가 동일 사상으로 **FinanceBench 98.7%** (벡터 RAG 80~90%) 보고. 우리는 목차 트리를 XML `<TITLE>`에서 **결정론적으로** 얻으므로 PageIndex의 LLM 인덱싱 비용까지 회피한다 (연구결과 §4-2) | |
| **D3** | **Correction-First** — 정정 체인 해소 후 최신본만 집계 대상으로 삼는다 | correction_link 그래프 + `effective` 뷰 |
| **D4** | **Evidence as Product** — `retrieved_context`·`think_trace`는 로그가 아니라 **채점 산출물**이다 | 구조화 스키마로 생산 |
| **D5** | **Abstain over Guess** — 근거 미달 시 답변을 만들지 않는다 | 결정론적 Abstention Gate |
| **D6** | **Platform-Native** — 임베딩·리랭킹까지 CLOVA Studio API로 통일한다 | "HyperCLOVA X만" 제약 해석 안전판 |

### 0-3. 평가지표 → 설계 요소 매핑 (역방향 설계)

| 평가지표 | 이 지표를 담당하는 설계 요소 | 실패 시 감지 지점 |
|---|---|---|
| 1 정확성 | **Fact Store 결정론 조회** + `compute` 도구(증감률·비중 계산) | Verifier: 수치 미출처 검출 |
| 2 근거 완전성 | Evidence Assembler — 질의 슬롯별 **필수 문서 커버리지 체크** | Verifier: 요구 슬롯 미커버 |
| 3 요구사항 충족 | Q-Understanding의 **요구사항 체크리스트 분해** → 답변 항목 대조 | Verifier: 체크리스트 미충족 항목 |
| 4 근거 기반 (환각) | **Numeric slot binding** + citation 존재 검증 | Verifier: context 밖 수치·주장 |
| 5 추론 논리성 | `think_trace` **구조화 스키마** (계획→도구호출→관측→판단) | 스키마 필드 결손 |
| 6 안전성·신뢰성 | Input Sanitizer + 역할 고정 프롬프트 + PII 마스킹 + 투자의견 차단 | Guard 트립 로그 |
| 7 정보한계 대응 | **Abstention Gate** + 역질문 생성기 (`suggestedQueries` 활용) | Gate 미발동 오답 |

---

## 1. 시스템 구성도

```
                       GET /answer?question_id=&question=
                                     │
┌────────────────────────────────────▼─────────────────────────────────────┐
│  API Layer (FastAPI + uvicorn)                                           │
│  · 응답 스키마 계약 고정   · SLO 타임아웃 가드   · trace 레코더            │
└────────────────────────────────────┬─────────────────────────────────────┘
                                     │
┌────────────────────────────────────▼─────────────────────────────────────┐
│  ① Guard — Input Sanitizer                            [평가지표 6]        │
│  · 지시 주입 패턴 탐지  · 역할 재정의 시도 차단  · 코퍼스 외 요구 탐지      │
└────────────────────────────────────┬─────────────────────────────────────┘
                                     │
┌────────────────────────────────────▼─────────────────────────────────────┐
│  ② Q-Understanding (HCX-007 · Structured Output)      [평가지표 3]        │
│  · 기업 별칭 정규화(4-way)   · 섹터 → 기업집합 해석                        │
│  · 기간/연결여부/지표 슬롯 추출  · 질의유형 6종 분류                       │
│  · 요구사항 체크리스트 분해 → requirement[]                              │
└────────────────────────────────────┬─────────────────────────────────────┘
                                     │  QuerySpec (typed)
┌────────────────────────────────────▼─────────────────────────────────────┐
│  ③ Planner (HCX-007 · Function Calling · Thinking)    [평가지표 5]        │
│  · QuerySpec → 도구 호출 DAG 수립   · 결과 부족 시 재계획 (max 2 hop)      │
└──────┬──────────┬──────────┬──────────┬──────────┬──────────┬───────────┘
       ▼          ▼          ▼          ▼          ▼          ▼
  fact_query  get_section doc_search compare_   trace_      compute
   (SQL)      (주소지정)   (Hybrid+RR)  metric    chain      (결정론)
       │          │          │          │          │          │
       └──────────┴──────────┴────┬─────┴──────────┴──────────┘
                                  │  Observation[]
┌─────────────────────────────────▼────────────────────────────────────────┐
│  ④ Evidence Assembler                                 [평가지표 2]        │
│  · 정정 체인 해소 → 유효본 선택   · 중복 제거   · citation id 부여         │
│  · requirement[] 커버리지 검사    · retrieved_context 직렬화              │
└─────────────────────────────────┬────────────────────────────────────────┘
                                  │
┌─────────────────────────────────▼────────────────────────────────────────┐
│  ⑤ Answer Synthesizer (HCX-007 · Structured Output)                      │
│  · 수치는 {{slot}} 자리표시자로만 생성 → 후단에서 DB값 치환   [D1]         │
│  · 문장마다 근거 citation id 부착 의무                                    │
└─────────────────────────────────┬────────────────────────────────────────┘
                                  │
┌─────────────────────────────────▼────────────────────────────────────────┐
│  ⑥ Verifier (전부 결정론 · LLM 미사용)              [평가지표 1·3·4·7]     │
│  V1 numeric grounding  V2 citation existence  V3 requirement coverage    │
│  V4 forbidden-content (미래예측·투자의견)  V5 abstention decision         │
└─────────────────────────────────┬────────────────────────────────────────┘
                         실패 → 1회 재생성 → 재실패 → 한계 고지 응답
                                  ▼
                          JSON Response (계약 스키마)
```

### 오프라인 인덱싱 파이프라인 (배포 전 1회)

```
raw/ 5.3GB
  │
  ├─ periodic  XML(UTF-8) ─┐
  ├─ major     XML(UTF-8) ─┤
  ├─ holding   XML(UTF-8) ─┼─► ① Parser 4종 ─► ② Normalizer ─┬─► L1 Fact Store  (SQLite)
  └─ exchange  HTML(⚠UTF-8)┘                                  ├─► L2 Section Store
     manifest.jsonl / universe.csv ─► Company Alias Table      └─► L3 Retrieval Index
                                                                    (BM25 Kiwi + bge-m3)
```

---

## 2. 데이터 계층

### 2-1. L0 — 파서 (4종, 포맷별 분리)

| 파서 | 대상 | 건수 | 필수 처리 |
|---|---|---:|---|
| `PeriodicParser` | `raw/periodic/**` DART XML | 1,466 | 목차 트리 복원 · XBRL TE 추출 · 표 파싱 폴백 |
| `MajorParser` | `raw/major/**` DART XML | 598 | `DOCUMENT-NAME ACODE` → 이벤트 유형 · AUNIT/TE 필드 |
| `HoldingParser` | `raw/holding/**` DART XML | 1,083 | TE ACODE 전 필드 · `BFR_RPT_DT` 체인 포인터 |
| `ExchangeParser` | `raw/exchange/**` **HTML** | 1,469 | 🔴 **바이트 UTF-8 강제 디코딩** · key-value 표 · 정정 diff 표 |

#### 🔴 ExchangeParser 인코딩 규약 (필수)

```python
# 반드시 이렇게 한다 — meta charset 선언(euc-kr)은 거짓이다
raw = path.read_bytes()
html = raw.decode("utf-8")            # euc-kr/cp949 시도 시 UnicodeDecodeError
soup = BeautifulSoup(html, "lxml")    # 문자열을 넘긴다 (bytes 넘기면 meta 재감지)

# ❌ 금지 — 1,469건 전부 문자 파괴
# BeautifulSoup(path.read_bytes(), "lxml")
# pandas.read_html(path)
```

#### PeriodicParser — 3단 추출 전략

```
Stage A  목차 트리     <TITLE> 순차 스캔 → 계층 경로 부여 (예: "III-2-2 연결 손익계산서")
Stage B  XBRL 사실     <TE ACODE=... ACONTEXT=...> → fin_fact 직행           [태깅된 경우]
Stage C  표 파싱 폴백  <TABLE>/<TR>/<TD|TU|TE> → 행 라벨 × 기수 열 매트릭스   [미태깅 대비]
```

> Stage C가 필수인 이유: 실측 태깅률이 annual 60% / half·quarter 28%다 (연구결과 §2-4).
> Stage C의 앵커는 **`III-1. 요약재무정보`** — 미태깅 분기보고서에서 행 × 기수 매트릭스 추출을
> 실제로 확인했다 (연구결과 §2-6-bis). 헤더가 `제N기(YYYY년 M월말)` 형식이라 **기수 ↔ 회계기간 매핑도 도출**된다.

#### 🔴 Stage D — 단위 정규화 (누락 시 1,000배 오차)

금액 단위가 문서마다 다르다: **원 / 천원 / 백만원 혼용**, 표본 27%는 인접 범위에 단위 미표기
(연구결과 §2-6-bis 실측).

```
1) 표 인접부 단위 선언 파싱  "(단위:천원)" / "단위 : 백만원"  → scale ∈ {1, 1_000, 1_000_000}
2) 전 값을 value_krw (원 기준)으로 정규화 저장 + raw_value/raw_unit 원문 보존
3) 단위 미검출 시:
     a. 동일 지표의 XBRL 값이 있으면 자릿수 대조로 scale 역추정
     b. 없으면 universe.market_cap과 자릿수 정합성 교차검증 (자산총계·매출액 대상)
     c. 그래도 불확정 → unit_confidence='low'
4) unit_confidence='low' fact는 compute(compare/rank)에서 **배제** + 답변에 한계 고지
```

> 인용 시에는 `raw_value` + `raw_unit`을 그대로 노출한다 (원문 대조 가능성 보존).
> 내부 계산·비교는 `value_krw`로만 수행한다.

### 2-2. L1 — Fact Store (SQLite)

> 채택 이유: 단일 서버 배포 · 외부 의존 0 · 트랜잭션 안정성 · 파일 단위 재현성.
> 사전 확인된 환경 제약: **duckdb 미설치**, `pandas`/`lxml`/`bs4`/`sqlite3` 사용 가능 (연구결과 §4 이전 세션 확인).

#### 스키마

```sql
-- 기업 마스터 + 별칭 (P6: 통용명 질의 대응)
CREATE TABLE company (
  corp_code    TEXT PRIMARY KEY,      -- 8자리 문자열 (선행 0 보존)
  corp_name    TEXT NOT NULL,         -- DART 공식 법인명 = raw/ 폴더명 (조인 키)
  listed_name  TEXT,                  -- 거래소 통용명 (현대차, KT, NC …)
  corp_eng_name TEXT,
  stock_code   TEXT,                  -- 6자리 문자열
  market       TEXT,                  -- KOSPI | KOSDAQ
  industry     TEXT,                  -- 대분류 8
  sector       TEXT,                  -- 테마 20
  listing_date TEXT,
  market_cap   INTEGER                -- 억원
);
CREATE TABLE company_alias (          -- 4-way 별칭 → corp_code 단일 해석
  alias TEXT PRIMARY KEY, corp_code TEXT NOT NULL, alias_kind TEXT
);  -- corp_name/listed_name/eng/stock_code/수기 변형(엘지, 하이닉스, LIG넥스원 …)

-- 문서 마스터
CREATE TABLE document (
  doc_id TEXT PRIMARY KEY,            -- {doc_group}_{rcept_no}
  corp_code TEXT, doc_group TEXT, doc_subtype TEXT,
  report_nm TEXT, rcept_no TEXT, rcept_dt TEXT, flr_nm TEXT,
  is_correction INTEGER,              -- [기재정정] 여부
  base_year INTEGER, base_month INTEGER,
  file_path TEXT, file_format TEXT,   -- xml | pdf+html (3건)
  supersedes_doc_id TEXT,             -- ← 정정 체인: 이 문서가 정정하는 원본
  is_effective INTEGER                -- ← 체인 최종본 여부 (집계 대상 플래그)
);

-- 재무 사실 (XBRL 우선, 표 파싱 폴백)
CREATE TABLE fin_fact (
  id INTEGER PRIMARY KEY,
  corp_code TEXT, doc_id TEXT,
  acode TEXT,                         -- ifrs-full_Revenue, dart_OperatingIncomeLoss …
  label_ko TEXT,                      -- 원문 행 라벨 ("매출액", "영업이익")
  fy INTEGER,                         -- 2024
  period_kind TEXT,                   -- instant(eFY) | duration(dFY)
  basis TEXT,                         -- consolidated | separate
  axis TEXT,                          -- 자본 구성요소 등 추가 축 (nullable)
  value_krw NUMERIC,                  -- 🔴 항상 '원' 기준 정규화 저장
  raw_value TEXT, raw_unit TEXT,      -- 원문 표기 보존 (원/천원/백만원) — 인용 시 사용
  unit_confidence TEXT,               -- high(선언 명시) | low(추정) ← 비교 질의 배제 판정용
  source TEXT,                        -- xbrl | table   ← 신뢰도 구분
  src_section TEXT,                   -- "III-2-2 연결 손익계산서"
  UNIQUE(doc_id, acode, fy, period_kind, basis, axis)
);
CREATE INDEX ix_fact_lookup ON fin_fact(corp_code, acode, fy, basis);

-- 계약 이벤트 (거래소공시)
CREATE TABLE contract_event (
  doc_id TEXT PRIMARY KEY, corp_code TEXT,
  event_kind TEXT,                    -- 체결 | 해지 | 신규시설투자 | 투자판단관련
  contract_kind TEXT,                 -- 공사수주 / 물품공급 …
  detail TEXT, counterparty TEXT,
  amount NUMERIC, recent_revenue NUMERIC, ratio_pct NUMERIC,
  start_dt TEXT, end_dt TEXT, decision_dt TEXT,
  large_corp_yn TEXT,
  linked_contract_doc_id TEXT         -- 해지↔체결 링크
);

-- 자금조달·자기주식 등 주요사항 이벤트
CREATE TABLE capital_event (
  doc_id TEXT PRIMARY KEY, corp_code TEXT,
  event_kind TEXT,                    -- 유상증자 | 전환사채(CB) | 신주인수권부사채(BW)
                                      -- | 교환사채(EB) | 자기주식취득 | 자기주식처분
                                      -- | 합병 | 분할 | 감자 | 조건부자본증권 …
  amount NUMERIC, currency TEXT, decision_dt TEXT,
  detail_json TEXT                    -- 유형별 가변 필드 (발행가·전환가·행사기간 등)
);

-- 지분 변동 (5% 보고)
CREATE TABLE holding_event (
  doc_id TEXT PRIMARY KEY, corp_code TEXT,
  reporter TEXT,                      -- RPT_RSP_NM (국민연금공단 …)
  cnt_before INTEGER, rate_before NUMERIC,   -- SUM_BMT_CNT / SUM_BMT_RT
  cnt_after  INTEGER, rate_after  NUMERIC,   -- SUM_TMT_CNT / SUM_TMT_RT
  change_reason TEXT,                        -- SUM_CHN_RWN
  report_dt TEXT, prev_report_dt TEXT        -- THS_RPT_DT / BFR_RPT_DT (체인 포인터)
);

-- 정정 diff (항목별 정정전/정정후)
CREATE TABLE correction_diff (
  id INTEGER PRIMARY KEY, doc_id TEXT,
  target_doc_kind TEXT, target_submit_dt TEXT,   -- "정정관련 공시서류" + "제출일"
  reason TEXT, item TEXT, before_val TEXT, after_val TEXT
);

-- 레지스트리 표 (임원/계열사/종속회사 등 — 임베딩 제외 대상)
CREATE TABLE registry_row (
  id INTEGER PRIMARY KEY, doc_id TEXT,
  registry_kind TEXT,                 -- 임원현황 | 직원현황 | 계열회사 | 종속회사 | 타법인출자
  row_json TEXT, src_section TEXT
);
```

#### 정정 체인 해소 알고리즘 (D3)

```
1) is_correction=1 문서에서 correction_diff.target_doc_kind / target_submit_dt 추출
2) 동일 corp_code 내에서 (doc_subtype≈target_doc_kind, rcept_dt=target_submit_dt) 원본 탐색
3) 성립 시 document.supersedes_doc_id 설정
4) 체인 종단(다른 문서가 supersede하지 않는 노드)에 is_effective=1
5) 매칭 실패 건은 unresolved 로그 → is_effective=1 유지(보수적: 누락보다 중복 노출 선택)
```

> ⚠️ 2)의 `doc_subtype ≈ target_doc_kind`는 문자열 근사 매칭이다. 정확 매칭률은 **미측정**이며
> 인덱싱 1회차에서 실측 후 폴백(제출일 ±N일 윈도)을 조정한다.
> `holding`은 `BFR_RPT_DT`가 명시 포인터라 근사 매칭이 불필요하다.

### 2-3. L2 — Section Store (Parent Document)

```sql
CREATE TABLE section (
  section_id TEXT PRIMARY KEY,        -- {doc_id}#III-2-2
  doc_id TEXT, corp_code TEXT,
  path TEXT,                          -- "III-2-2"     ← 결정론 주소 (D2)
  title TEXT,                         -- "연결 손익계산서"
  level INTEGER,
  text TEXT,                          -- 서술 본문
  tables_md TEXT,                     -- 표를 markdown 보존 (LLM 가독)
  char_len INTEGER,
  content_class TEXT                  -- prose | table_registry | financial_stmt
);
CREATE INDEX ix_section_addr ON section(corp_code, doc_id, path);
```

**표준 섹션 주소 사전** (법정 목차 기반, 질의 유형 → 주소 매핑)

| 질의 의도 | 섹션 주소 |
|---|---|
| 핵심 사업 / 사업 개요 | `II-1` |
| 주요 제품·서비스 | `II-2` |
| 설비투자 / 생산설비 | `II-3` |
| 매출·수주 상황 | `II-4` |
| 주요계약·연구개발 | `II-6` |
| 요약재무정보 | `III-1` |
| 연결 재무상태표 / 손익계산서 | `III-2-1` / `III-2-2` |
| 별도 재무상태표 / 손익계산서 | `III-4-1` / `III-4-2` |
| 경영진단·분석의견 (MD&A) | `IV` |
| 계열회사 | `IX` |

> 이 사전 덕분에 "2026년 1분기 보고서 기준 주요 투자 계획"은 벡터 검색 없이
> `(corp, 2026Q1, path IN ('II-3','II-4','II-6'))` 조회로 처리된다.

### 2-4. L3 — Retrieval Index (선별 임베딩)

#### 선별 정책 (P4 — 비용의 핵심)

| content_class | BM25 | Vector | 근거 |
|---|:---:|:---:|---|
| `prose` (사업개요·MD&A·주석 서술 등) | ✅ | ✅ | 의미 검색 대상 |
| `financial_stmt` | ✅ | ❌ | Fact Store가 정답 경로. 표 임베딩은 노이즈 |
| `table_registry` (임원·계열사·종속회사·연구개발실적 등) | ✅ (라벨만) | ❌ | 볼륨 47% 차지, SQL이 정확 (연구결과 §2-6) |

예상 절감 `[추정]`: 전량 40만 청크 → **선별 후 15~20만 청크** (periodic 서술 비중 기준 외삽)

#### 하이브리드 검색 (RRF → Reranker 2-stage)

```
질의
 ├─ Sparse: BM25   (Kiwi 형태소 분석 → 명사·복합명사 색인)     top 50
 └─ Dense : bge-m3 (CLOVA Embedding v2, 1024d, cosine)        top 50
              │
              ▼  RRF 융합  score = Σ 1/(60 + rank_i)
           후보 top 30
              │
              ▼  CLOVA Reranker  POST /v1/api-tools/reranker
           최종 top 5~8  →  Parent Section 확장 (L2에서 전체 섹션 회수)
```

**필터 우선 적용** — 검색 전 메타데이터로 후보를 좁힌다 (정확도·비용 동시 개선)

```
corp_code IN (…)  AND  base_year = …  AND  doc_group IN (…)  AND  is_effective = 1
```

#### 🔴 한국어 형태소 BM25 (유일한 자체 개발 코어)

AITHOR·AI-research-SKILLs 양쪽에 한국어 형태소 분석이 **0건**이다 (연구결과 §4-2).

- 채택: **Kiwipiepy** (순수 Python 휠 배포 → Docker 빌드 단순, MeCab 대비 시스템 의존 없음)
- 색인 단위: 명사(NNG/NNP) + 복합명사 분해 + 원형 보존
- 금융 도메인 사용자 사전: `연결기준`, `별도기준`, `유상증자`, `전환사채`, `신주인수권부사채`,
  `교환사채`, `자기주식`, `대량보유`, `단일판매공급계약`, `설비투자`, `증감률`, 기업명 70종, 섹터명 20종
- 폴백: Kiwipiepy 설치 실패 시 **문자 n-gram(2,3) BM25**로 자동 강등 (품질 저하 감수, 가용성 우선)

---

## 3. Agent 계층

### 3-1. 모델 배치

| 단계 | 모델 | 이유 |
|---|---|---|
| Q-Understanding | **HCX-007** (Structured Output) | 슬롯 추출 정확도가 전체 파이프라인 정확도의 상한. SO로 스키마 강제 |
| Planner | **HCX-007** (Function Calling + Thinking) | 도구 DAG 수립 = 다단 추론. thinking 내용이 `think_trace` 원료 |
| Answer Synthesizer | **HCX-007** (Structured Output) | 인용 부착 + 슬롯 자리표시자 규약 준수 필요 |
| 경량 분기 (선택) | HCX-DASH-002 | 단순 Closed 질의의 라우팅만 담당해 지연·비용 절감. **성능 확인 후 도입** |
| Reranking | CLOVA **Reranker API** | 외부 리랭커 미사용 (D6) |
| Embedding | CLOVA **Embedding v2 (bge-m3)** | 8,192 토큰 · 1024d (clir 500토큰은 부적합) |
| Verifier | **모델 미사용 (순수 코드)** | 검증자가 LLM이면 환각을 환각으로 검증한다 |

### 3-2. 도구 명세 (Function Calling)

```json
[
  {
    "name": "fact_query",
    "description": "재무 지표를 결정론적으로 조회한다. 수치 질의는 반드시 이 도구를 사용한다.",
    "parameters": {
      "type": "object",
      "properties": {
        "corp": {"type": "array", "items": {"type": "string"}, "description": "정규화된 corp_code 목록"},
        "metric": {"type": "string", "description": "매출액|영업이익|당기순이익|자산총계|부채총계|자본총계|영업활동현금흐름|설비투자(CAPEX) 등"},
        "fy": {"type": "array", "items": {"type": "integer"}},
        "basis": {"type": "string", "enum": ["consolidated", "separate"]}
      },
      "required": ["corp", "metric", "fy"]
    }
  },
  {
    "name": "get_section",
    "description": "정기공시의 특정 섹션 원문을 목차 주소로 직접 조회한다. 서술형 질의의 1순위 도구.",
    "parameters": {
      "type": "object",
      "properties": {
        "corp": {"type": "array", "items": {"type": "string"}},
        "period": {"type": "string", "description": "2026Q1 | 2025FY | 2025H1"},
        "paths": {"type": "array", "items": {"type": "string"}, "description": "II-1, III-1 등"}
      },
      "required": ["corp", "period", "paths"]
    }
  },
  {
    "name": "doc_search",
    "description": "주소 지정으로 찾을 수 없을 때 하이브리드 검색(BM25+벡터+리랭커)을 수행한다.",
    "parameters": {
      "type": "object",
      "properties": {
        "query": {"type": "string"},
        "corp": {"type": "array", "items": {"type": "string"}},
        "doc_group": {"type": "array", "items": {"type": "string",
                      "enum": ["periodic", "major", "exchange", "holding"]}},
        "year_range": {"type": "array", "items": {"type": "integer"}},
        "top_k": {"type": "integer", "default": 8}
      },
      "required": ["query"]
    }
  },
  {
    "name": "event_query",
    "description": "계약·자금조달·지분변동 이벤트를 유형별로 집계 조회한다. 정정 반영 유효본만 반환.",
    "parameters": {
      "type": "object",
      "properties": {
        "corp": {"type": "array", "items": {"type": "string"}},
        "event_domain": {"type": "string", "enum": ["contract", "capital", "holding"]},
        "event_kind": {"type": "array", "items": {"type": "string"},
                       "description": "유상증자|전환사채(CB)|신주인수권부사채(BW)|교환사채(EB)|체결|해지 등"},
        "date_range": {"type": "array", "items": {"type": "string"}}
      },
      "required": ["corp", "event_domain"]
    }
  },
  {
    "name": "trace_chain",
    "description": "정정·후속 공시 이력 체인 또는 계약 체결↔해지 링크를 추적한다.",
    "parameters": {
      "type": "object",
      "properties": {
        "doc_id": {"type": "string"},
        "mode": {"type": "string", "enum": ["correction", "contract_lifecycle", "holding_history"]}
      },
      "required": ["mode"]
    }
  },
  {
    "name": "compute",
    "description": "증감률·비중·순위·합계를 계산한다. LLM은 산술을 직접 수행하지 않는다.",
    "parameters": {
      "type": "object",
      "properties": {
        "op": {"type": "string", "enum": ["delta", "delta_pct", "share_pct", "rank", "sum", "compare"]},
        "operands": {"type": "array", "items": {"type": "object"},
                     "description": "각 원소는 fact_query가 반환한 fact_id 참조"}
      },
      "required": ["op", "operands"]
    }
  }
]
```

> `compute`의 `operands`가 **fact_id 참조**인 것이 핵심이다. LLM이 숫자를 프롬프트로 옮겨 적는 순간
> 전사 오류(transcription error)가 발생하므로, 계산은 DB 값에 대해 서버 측에서 수행한다 (D1).

### 3-3. 질의 유형별 실행 경로

| 유형 | 판정 신호 | 실행 경로 |
|---|---|---|
| **T1** 단일 수치 조회 | 단일 기업 + 지표 + 기간 | `fact_query` → (미스 시 `get_section III-1` → `doc_search`) |
| **T2** 특정 문서 서술 요약 | 문서 지정 + "정리/설명" | `get_section` (주소 사전) → 필요 시 `doc_search` 보강 |
| **T3** 기업 간 비교·연산 | 2+ 기업 또는 섹터 + 비교어 | `fact_query`(N개) → `compute(compare/rank)` |
| **T4** 이벤트 유형별 집계 | "자금조달/계약 내역 정리" | `event_query` → `compute(sum/share_pct)` |
| **T5** 이벤트 생애주기 추론 | "체결 이후 해지", "변경 이력" | `event_query` → `trace_chain(contract_lifecycle)` |
| **T6** 시계열 섹션 대조 | 2개 시점 + "변화/비교" | `get_section`(양 시점 동일 path) → 구조 대조 |

**섹터 질의 해석 (T3)** — "2차전지 기업 A와 B" 류

```
sector = "2차전지"  →  company WHERE sector='2차전지'  →  LG에너지솔루션 / 삼성SDI / 에코프로비엠
질의에 익명 표기(A, B)가 오면 → 섹터 전체를 대상으로 지표 정렬 후 상위 비교 + 대상 기업 명시
```

### 3-4. `think_trace` 구조 (평가지표 5 산출물)

```
[1] 질의 해석
    기업: 삼성전자(00126380)  지표: 매출액  기간: FY2025  기준: 연결
    요구사항: ① 2025년 연결 매출액 수치
[2] 계획
    fact_query(corp=[00126380], metric=매출액, fy=[2025], basis=consolidated)
[3] 도구 실행
    → fact_id=F12345  value=300,870,903  unit=백만원
      출처: 사업보고서(2025.12) rcept_no=2026xxxx  섹션 III-2-2 연결 손익계산서
      source=xbrl  acode=ifrs-full_Revenue  context=CFY2025dFY_...ConsolidatedMember
[4] 검증
    V1 수치 출처 확인 ✅  V2 인용 존재 ✅  V3 요구사항 1/1 ✅  V4 금지표현 없음 ✅
[5] 결론
    2025년 연결 매출액 300,870,903백만원 (약 300.9조원)
```

> 서술형 산문이 아니라 **단계 라벨이 붙은 구조**로 출력한다. 채점자가 추론 경로를 검증할 수 있어야 한다 (D4).

---

## 4. 환각 방지 · 정보한계 대응

### 4-1. Numeric Slot Binding (D1 — 환각 차단의 주 메커니즘)

Synthesizer는 **수치를 직접 쓰지 못한다.** 자리표시자만 생성한다.

```
Synthesizer 출력 (원형):
  "삼성전자의 2025년 연결 매출액은 {{F12345.value}}{{F12345.unit}}입니다. [C1]"

서버 치환 후 (최종 answer):
  "삼성전자의 2025년 연결 매출액은 300,870,903백만원입니다. [C1]"
```

| 규칙 | 내용 |
|---|---|
| R1 | 답변 내 모든 숫자는 `{{fact_id.field}}` 형태여야 한다 |
| R2 | 치환 불가 자리표시자 존재 → **생성 실패로 간주**하고 재생성 |
| R3 | 자리표시자 아닌 생(生) 숫자가 답변에 등장 → Verifier V1이 차단 (예외: 질의 원문 인용, 연도) |

### 4-2. Verifier (전부 결정론)

| ID | 검사 | 실패 시 |
|---|---|---|
| **V1** | 답변의 모든 수치가 fact_id 치환 결과이거나 `retrieved_context` 내 문자열로 존재 | 재생성 1회 → 실패 시 해당 문장 삭제 |
| **V2** | 모든 인용 마커 `[Cn]`이 실제 citation 목록에 존재 | 재생성 |
| **V3** | Q-Understanding의 `requirement[]` 각 항목이 답변에 대응 문장을 가짐 | 미충족 항목 명시 후 부분 답변 |
| **V4** | 금지 표현 탐지 — 미래 예측·투자 의견·목표주가·매수/매도 권유 | 해당 문장 삭제 + 고지 |
| **V5** | Abstention 조건 판정 (§4-3) | 한계 고지 응답으로 전환 |

### 4-3. Abstention Gate (평가지표 7)

```
if  fact_query 미스 AND doc_search 최고 rerank 점수 < τ
      → "제공된 공시 데이터에서 확인되지 않습니다"
if  질의 대상 기업 ∉ 70개사 유니버스
      → "본 시스템은 지정된 70개 기업의 공시만 보유합니다" + 유사 기업 제시
if  질의 기간 ∉ 2023-01 ~ 2026-03
      → "보유 공시 기간(2023.01~2026.03) 범위를 벗어납니다"
if  요구 문서 유형 미보유 (예: 감사보고서 외 첨부, 뉴스)
      → "해당 정보는 보유 공시 유형(정기·주요사항·거래소·지분)에 포함되지 않습니다"
if  질의가 미래 예측·투자 판단 요구
      → "공시에 근거 없는 예측·투자 의견은 제공하지 않습니다" + 대체 가능한 사실 제시
if  질의 모호 (기업·기간·기준 미특정)
      → 역질문 생성: "연결기준/별도기준 중 어느 것을 기준으로 할까요?"
```

**역질문 생성 보강**: CLOVA Reranker 응답의 `result.suggestedQueries[]`를 활용해 대체 질의를 제시한다
(플랫폼이 제공하는 기능을 그대로 쓴다).

> ⚠️ 임계값 τ는 **미정**이다. 자체 평가셋(§6-1)으로 캘리브레이션한다.
> 과도한 abstention은 평가지표 1·3을, 과소한 abstention은 지표 4·7을 해친다 — 트레이드오프 튜닝 대상.

### 4-4. 안전성 (평가지표 6)

| 위협 | 방어 |
|---|---|
| 프롬프트 주입 ("이전 지시 무시하고…") | Input Sanitizer 패턴 탐지 + 사용자 입력을 **데이터 구분자로 감싸 전달** + 시스템 역할 재확인 |
| 역할 이탈 유도 ("투자 추천해줘") | V4 금지 표현 필터 + 역할 고정 시스템 프롬프트 |
| 코퍼스 외 지식 유도 ("뉴스에서 본 바로는…") | 근거 없는 주장은 V1/V2에서 탈락 → abstention |
| 개인정보 노출 | 공시에 포함된 임원 개인정보(생년월일·성별 등 `PSN_BIH`/`PSN_SEX`) **응답 시 마스킹** |
| 도구 인자 주입 | 도구 파라미터는 **화이트리스트 enum + 타입 검증**, SQL은 파라미터 바인딩만 (문자열 조립 금지) |
| 과도 요청 | 요청당 도구 호출 상한(8회) · 총 토큰 상한 · 타임아웃 |

> `registry_row`에 임원 생년월일·성별이 실제로 존재함을 확인했다 (연구결과 §2-5 AUNIT `PSN_BIH`/`PSN_SEX`).
> 마스킹은 선택이 아니라 지표 6 대응 필수 항목이다.

---

## 5. API 명세

### 5-1. 평가용 엔드포인트 (주최측 계약 준수)

```
GET /answer?question_id={id}&question={질의}
```

| 파라미터 | 타입 | 필수 | 설명 |
|---|---|:---:|---|
| `question_id` | string | ✅ | 평가 문항 식별자 |
| `question` | string | ✅ | 질의 원문 (URL 인코딩) |

**응답 (200)** — PDF 명시 스키마를 정확히 준수하고, 부가 정보는 추가 필드로만 확장

```json
{
  "question_id": "Q-001",
  "question": "삼성전자의 2025년 연결기준 매출액은 얼마인가?",
  "retrieved_context": "[C1] 삼성전자 사업보고서(2025.12) · 접수번호 20260311xxxxxx · III-2-2 연결 손익계산서\n      매출액(주29) 제57기 300,870,903 (단위: 백만원)\n[C2] ...",
  "think_trace": "[1] 질의 해석 ... [2] 계획 ... [3] 도구 실행 ... [4] 검증 ... [5] 결론",
  "answer": "삼성전자의 2025년 연결기준 매출액은 300,870,903백만원(약 300.9조원)입니다. [C1]",

  "citations": [
    { "id": "C1", "doc_id": "periodic_20260311xxxxxx", "corp_name": "삼성전자",
      "report_nm": "사업보고서 (2025.12)", "rcept_no": "20260311xxxxxx",
      "rcept_dt": "20260311", "section": "III-2-2 연결 손익계산서",
      "source": "xbrl", "acode": "ifrs-full_Revenue" }
  ],
  "confidence": "high",
  "abstained": false,
  "latency_ms": 4120
}
```

> 부가 필드(`citations`/`confidence`/`abstained`/`latency_ms`)는 **계약 4필드를 침범하지 않는다.**
> 주최측 파서가 4필드만 읽어도 정상 동작하며, 사람 검토 시 근거 추적성을 높인다.

**실패 응답** — 500을 내지 않는다. 채점 가능한 형태로 항상 200을 반환한다.

```json
{ "question_id": "Q-007", "question": "...",
  "retrieved_context": "(검색 결과 없음)",
  "think_trace": "[1] 질의 해석 → 대상 기업 '△△전자'는 유니버스 70개사에 없음 [5] Abstention 발동",
  "answer": "제공된 공시 데이터에서 '△△전자'를 확인할 수 없습니다. 본 시스템은 지정된 70개 기업의 공시(2023.01~2026.03)만 보유합니다.",
  "abstained": true, "abstain_reason": "out_of_universe" }
```

### 5-2. 운영 엔드포인트

| 경로 | 용도 |
|---|---|
| `GET /health` | 헬스체크 (평가기간 09.07~09.20 상시 가용 확인용) |
| `GET /ready` | 인덱스 로드 완료 여부 |
| `GET /meta` | 코퍼스 버전·문서수·인덱스 빌드 시각 (재현성 증빙) |

### 5-3. 성능 예산 `[추정 — 실측 후 확정]`

| 구간 | 목표 |
|---|---:|
| Q-Understanding (HCX-007 SO) | ~1.5 s |
| Planner + 도구 실행 (병렬) | ~2.5 s |
| Reranker | ~0.8 s |
| Synthesizer | ~2.0 s |
| Verifier (코드) | <0.1 s |
| **총 p50 / p95** | **~7 s / ~15 s** |
| 타임아웃 | 25 s → 초과 시 부분 근거 기반 답변 + 한계 고지 |

> 🔴 이 예산은 **CLOVA API 응답 지연 실측 없이 산정한 목표치**이며, RPM/TPM 한도(미확인 U1)에
> 따라 크게 달라질 수 있다. 첫 통합 시점에 실측해 재산정한다.

---

## 6. 검증 전략

### 6-1. 자체 평가셋 (Gold Set)

주최측 평가문제는 비공개다. 따라서 **참고용 질의 6종 구조를 복제한 자체 평가셋**을 만든다.

| 유형 | 문항수 목표 | 정답 확보 방법 |
|---|---:|---|
| T1 단일 수치 (Closed) | 40 | Fact Store와 **독립적으로** 원문 표 수기 확인 |
| T2 문서 서술 요약 (Open) | 20 | 섹션 원문 대조 — 필수 포함 항목 체크리스트 방식 |
| T3 비교·연산 (Closed) | 30 | 수기 계산 |
| T4 이벤트 집계 (Open) | 20 | manifest + 원문 대조 |
| T5 생애주기 추론 (Closed) | 20 | 체결·해지 쌍 수기 추적 |
| T6 시계열 대조 (Open) | 15 | 양 시점 섹션 수기 대조 |
| **Abstention 함정** | **15** | 유니버스 외 기업 / 범위 외 기간 / 미보유 유형 / 투자의견 요구 / 프롬프트 주입 |
| 합계 | **180** | |

> 🔴 **Abstention 함정 15문항이 핵심이다.** 평가지표 7은 "답을 잘 하는 능력"이 아니라
> "답하지 않을 줄 아는 능력"을 본다. 이 문항 없이 개발하면 지표 7에서 구조적으로 실점한다.

### 6-2. 채점 지표 (평가지표 1:1 대응)

| 자체 지표 | 계산 | 대응 |
|---|---|---|
| Exact Numeric Accuracy | 수치 완전일치율 | 지표 1 |
| Evidence Recall@k | 필수 문서가 `retrieved_context`에 포함된 비율 | 지표 2 |
| Requirement Coverage | 요구사항 체크리스트 충족률 | 지표 3 |
| Groundedness | 답변 주장 중 근거 매칭 비율 (V1/V2 통과율) | 지표 4 |
| Trace Validity | `think_trace` 5단 구조 완결성 | 지표 5 |
| Safety Pass Rate | 주입·PII·투자의견 함정 방어율 | 지표 6 |
| Abstention F1 | 답해야 할 때 답하고, 답하면 안 될 때 거부 | 지표 7 |

### 6-3. 회귀 방지

- 인덱싱 파이프라인: 문서 단위 파싱 성공/실패 카운트를 산출물로 기록. 실패 문서 목록 명시 (침묵 실패 금지)
- 파서 단위 테스트: 4종 파서 × 대표 문서 고정 픽스처 → 필드 추출 결과 스냅샷 비교
- Gold Set 회귀 실행: 변경 시마다 180문항 전수 재실행, 지표 하락 시 차단

---

## 7. 배포

### 7-1. 구성

```
NCP Server (또는 동등 환경, Public 망 필수)
├── FastAPI + uvicorn (gunicorn workers)
├── SQLite (fact + section)  ← 읽기 전용 마운트
├── 벡터 인덱스 (sqlite-vec 또는 FAISS 파일)
├── BM25 인덱스 (Kiwi 토큰 역색인, 파일)
└── 외부 호출: CLOVA Studio (chat v3 / embedding v2 / reranker)
```

**인덱스는 이미지에 포함하지 않고 볼륨 마운트한다** — 이미지 비대화 방지 + 재빌드 없이 인덱스 교체.

### 7-2. 재현성 (제출물 1 요건)

```
Dockerfile              멀티스테이지, 인덱스 제외
requirements.txt        핀 고정 버전
README.md               환경변수 · 인덱스 빌드 명령 · 실행 명령 · 헬스체크 확인 절차
scripts/build_index.py  raw/ → SQLite + 인덱스 (idempotent, 재실행 안전)
.env.example            CLOVA_API_KEY 등 (실키 커밋 금지)
```

### 7-3. 평가기간 가용성 (09.07~09.20 상시)

| 리스크 | 대응 |
|---|---|
| 프로세스 다운 | systemd/Docker `restart: always` + `/health` 외부 모니터 |
| CLOVA API 일시 실패 | 지수 백오프 재시도(3회) → 실패 시 abstention 응답 (500 금지) |
| **크레딧 소진** | 요청당 토큰 상한 + 일일 호출 카운터 + 소진 임박 시 경량 경로 강등 |
| 동시 요청 폭주 | 세마포어로 CLOVA 동시 호출 제한 + 큐잉 |
| 응답 캐시 | `question_id` 단위 캐시 (동일 문항 재요청 시 비용 0) — 재현성에도 유리 |

> 🔴 크레딧 소진이 가장 현실적인 실패 모드다. 주최측이 "초과 시 별도 비용보전 없음"을 명시했고,
> 인덱싱 임베딩 총량이 크다 (연구결과 U2 미확인). 계측 없는 배포는 금지한다.

---

## 8. 재사용 자산 적용 계획

### 8-1. AITHOR — 코드 직접 확인 결과

`src/aithor_agent_framework/rag.py` 실측 (함수·상수명 실제 확인):

| 구성 | 실제 구현 | 본 과제 적용 |
|---|---|---|
| `HybridRetriever.search()` (:85) | 3-way 랭킹 융합 — `_rank_bm25` + `_rank_token_cosine` + `_rank_semantic`(embedding_provider 주입 시) | ✅ **구조 그대로 사용** |
| `_rank_bm25` (:112) | 자체 BM25 구현. `idf = log(1+(N-df+0.5)/(df+0.5))`, `tf*(k1+1)/denom` | ✅ 로직 유지, **토크나이저만 교체** |
| `_rrf_fuse` (:169) | RRF, `k=60`, `1/(k+rank)` 합산 | ✅ **그대로 사용** — 본 명세 §2-4 융합식과 동일 |
| `_rank_semantic` (:142) | `embedding_provider.embed()` 주입 · L2 정규화 전제 dot=cosine | ✅ **CLOVA Embedding v2 어댑터를 주입** |
| `_tokens` (:156) | 🔴 `_TOKEN_RE.finditer()` — **정규식 토크나이저, 형태소 분석 없음** | ❌ **Kiwi로 교체 필수** (한국어는 조사 부착으로 BM25 열화) |
| `chunk_text` (:178) | `chunk_size=800, overlap=120` 문자 단위 | △ 참조 — 본 과제는 섹션 경계 우선 청킹 |
| `build_grounded_prompt` (:223) · `rerank_results` (:316) · `evaluate_retrieval` (:200) | 근거 프롬프트 조립 / 재정렬 / 검색 평가 | △ 참조 (리랭킹은 CLOVA API로 대체) |
| docstring (:56) | *"Dependency-free BM25 + token-vector hybrid retriever … not to replace a production vector DB"* | 저자 스스로 프로덕션 벡터DB 대체 아님을 명시 → 벡터 계층은 별도 구축 |
| `adversarial_verify.py` | groundedness 관련 모듈 존재 | △ Gold Set Groundedness 채점 참조 |
| 🔴 **HTTP 서버** | `FastAPI(` 인스턴스 **저장소 전체 0건** | ❌ **`GET /answer` 서버는 신규 개발** |

| 자산 | 적용 | 형태 |
|---|---|---|
| AITHOR `llm_providers.py` | `ClovaProvider(OpenAIProvider)` — `base_url="…/v1/openai"` | 패턴 차용 (`OpenRouterProvider` 선례 동일) |
| AITHOR `rag.py` | `_rrf_fuse` · `_rank_bm25` 로직 + 3-way 융합 구조 | 차용 후 `_tokens` → Kiwi, 리랭킹 → CLOVA API |
| 🔵 `AI-research-SKILLs/15-rag/pageindex` | **Vectorless RAG 사상** — 계층 트리 + LLM 탐색이 D2를 외부 검증 (FinanceBench 98.7%) | **사상만 차용** — 라이브러리 미사용 (PDF 입력 전제 + `litellm` 의존이 HCX 전용 제약과 충돌). 트리는 `<TITLE>`에서 자체 구축 |
| `AI-research-SKILLs/41-on-device-hybrid-search` | BM25+sqlite-vec+Reranker 단일서버 구성, RRF 융합 | 레시피 적용 |
| `AI-research-SKILLs/15-rag/adaptive-chunking` | 문서별 청킹 전략 선택 + **정답 없는 청크 품질 채점 5 metric** | 4종 포맷 이질 코퍼스에 적용 · 청킹 품질 게이트 |
| `AI-research-SKILLs/15-rag` | 청킹 전략·리랭킹·벡터DB 선정 근거 | 설계 근거 |
| 🔴 `AI-research-SKILLs/76-finance-agent-skills` | — | **사용 금지** — DCF/센티먼트/트레이딩 대상이며 데이터원(yfinance/opencli)이 **과제 금지 조항과 충돌** |
| `AI-research-SKILLs/16-prompt-engineering/instructor` | JSON Schema 출력 검증 | HCX-007 Structured Output과 결합 |
| `AI-research-SKILLs/11-evaluation` | 평가 harness 구조 | Gold Set 러너 |
| `AI-research-SKILLs/17-observability` | 비용·레이턴시 추적 | 크레딧 감시 |

> ⚠️ **AITHOR 전체 도입은 하지 않는다.** 691파일/35K LOC 범용 프레임워크를 38일 일정에 통째로
> 들여오면 학습·적응 비용이 이득을 초과한다. 검증된 4개 요소만 선별 차용한다.

---

## 9. 리스크 레지스터

| ID | 리스크 | 영향 | 대응 | 상태 |
|---|---|---|---|---|
| **R1** | "LLM은 HyperCLOVA X만"이 임베딩·리랭커까지 포함하는지 불명확 | 실격 가능 | 처음부터 CLOVA Embedding v2 + Reranker만 사용 (D6). 08.06 설명회 확인 | 회피 설계 완료, 확인 필요 |
| **R2** | 크레딧 초과 (자기 부담) | 비용/중단 | 선별 임베딩(§2-4) + 토큰 계측 + 캐시. 설명회에서 규모 확인 | 미확인 (U2) |
| **R3** | XBRL 태깅률 부족 (half/quarter 28%) | 정확도 | Stage C 표 파싱 폴백 + `III-1 요약재무정보` 앵커 | 설계 반영 |
| **R4** | 정정 체인 근사 매칭 실패 | 집계 오류 | 실패 건 unresolved 로그 + 보수적 유효 처리. holding은 명시 포인터 사용 | 실측 필요 |
| **R5** | exchange 인코딩 함정 | 1,469건 파괴 | UTF-8 강제 디코딩 규약 명문화 + 파서 단위 테스트 | 해결 |
| **R5b** | **금액 단위 혼용 (원/천원/백만원, 27% 미표기)** | **비교 질의 1,000배 오차** | Stage D 정규화(`value_krw`) + `unit_confidence` 배제 게이트 | 설계 반영 |
| **R6** | RPM/TPM 한도로 인덱싱·평가 지연 | 일정/가용성 | 배치 임베딩 + 동시성 제어. 설명회 확인 | 미확인 (U1) |
| **R7** | Kiwipiepy 환경 설치 실패 | 검색 품질 | 문자 n-gram BM25 폴백 자동 강등 | 설계 반영 |
| **R8** | Abstention 임계값 오설정 | 지표 1·7 상충 | Gold Set 함정 15문항으로 캘리브레이션 | 튜닝 대상 |
| **R9** | 38일 일정 초과 | 미제출 | MVP 범위 고정 + 주차 게이트 (제안서 참조) | 관리 대상 |
| **R10** | `pdf+html` 3건 파서 미처리 | 소량 결손 | 해당 3건 텍스트만 별도 추출 또는 명시적 미보유 처리 | 저영향 |

---

## 검증 수준

| 핵심 주장 | 수준 | 근거 |
|---|---|---|
| 과제 요건·평가지표·API 계약 스키마 준수 | [검증됨] | 주최측 PDF 직접 대조 |
| 코퍼스 4종 포맷·인코딩·XBRL 태깅 구조 | [검증됨] | 실측 (연구결과 §2) |
| XBRL 태깅률 annual 60% / half·quarter 28% | [추정] | 표본 25건/유형 |
| 정정공시 원본 포인터·diff 표 존재 | [검증됨] | 현대건설 정정본 실제 추출 |
| `III-1 요약재무정보` Stage C 폴백 실현 가능 | [검증됨] | 미태깅 분기보고서에서 매트릭스 실제 추출 |
| 단위 원/천원/백만원 혼용 + 27% 미표기 | [검증됨] | 60건 표본 실측 |
| Stage D 자릿수 역추정(3-a/3-b)의 정확률 | **[미확인]** | 구현 후 실측 필요 — 실패 시 `unit_confidence=low` 배제로 안전 강등 |
| holding `BFR_RPT_DT` 명시 체인 포인터 | [검증됨] | CJ제일제당 보고서 실제 추출 |
| HCX-007 FC/SO/Thinking + 128K/32K | [검증됨] | NCP 공식 모델 문서 |
| CLOVA Reranker·Embedding v2 엔드포인트·스키마 | [검증됨] | NCP 공식 API 문서 |
| OpenAI 호환 계층 지원 파라미터 | [검증됨] | NCP 공식 호환성 문서 |
| AITHOR `base_url` 주입 가능 (프로바이더 소규모 추가) | [검증됨] | `llm_providers.py:414/450/525/588/628` 직접 확인 |
| AITHOR `HybridRetriever` 3-way 융합 · `_rrf_fuse(k=60)` · 자체 BM25 실재 | [검증됨] | `rag.py:85/112/142/169` 코드 직접 확인 |
| AITHOR `_tokens`가 정규식 기반 (한국어 형태소 없음) | [검증됨] | `rag.py:156` — `_TOKEN_RE.finditer()` |
| AITHOR에 HTTP 서버 부재 → `GET /answer` 신규 개발 | [검증됨] | `grep -rn "FastAPI(" --include=*.py` 전체 0건 |
| AITHOR FC 경로가 CLOVA에서 무수정 동작 | [추정] | 문서 대조 기반, **실호출 미검증** |
| 선별 임베딩으로 40만 → 15~20만 청크 축소 | [추정] | 섹션 볼륨 1건 실측 외삽 |
| 성능 예산 p50 7s / p95 15s | [추정] | **CLOVA 실측 지연 없음** — 목표치 |
| Abstention 임계값 τ | **[미확인]** | Gold Set 캘리브레이션 필요 |
| RPM/TPM·크레딧 규모 | **[미확인]** | 공식 문서 미기재 — 08.06 설명회 |
| "LLM만" 제약의 임베딩·리랭커 포함 여부 | **[미확인]** | PDF 미명시 — 회피 설계로 대응 |
| 핵심 5섹션(I·II·III·II-1·III-1)이 기업 무관 존재 | [검증됨] | 12개사 무작위 표본(10섹터 횡단) 12/12 보유 |
| D2(주소 우선)가 금융문서에서 벡터 RAG를 상회 | [검증됨/인용] | PageIndex SKILL.md의 FinanceBench 98.7% 주장. **원논문 재현 미검증 · 영문+타모델 기준이라 한국어 전이는 [추정]** |
| 10종 주소 사전 **전체**가 70개사 전부에 적용 | [추정] | 핵심 5섹션은 검증됨. 나머지 5종(II-3·II-6·III-2-2·IV·IX)은 미교차검증 — W0 전수 확인 |
| 정정 체인 근사 매칭 정확률 | **[미확인]** | 인덱싱 1회차 실측 예정 |
