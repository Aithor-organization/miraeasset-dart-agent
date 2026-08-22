# SPEC — 공시 Agent 구현 명세 (실행 가능 수준)

> 근거: [`../proposal/01-technical-specification.md`](../proposal/01-technical-specification.md)
> 본 문서는 **코드로 직역 가능한 수준**의 계약 명세다. 산문 설명은 제안서에, 계약은 여기에.

---

## 0. 범위 경계 (오늘 세션에서 완결 가능한 것 / 불가한 것)

| 계층 | API 키 필요 | 오늘 완결 | 비고 |
|---|:---:|:---:|---|
| L0 파서 4종 | ❌ | ✅ | 결정론 |
| L1 Fact Store + 정정 체인 + 별칭 | ❌ | ✅ | 결정론 |
| L2 Section Store | ❌ | ✅ | 결정론 |
| L3-a BM25 (한국어 형태소) | ❌ | ✅ | kiwipiepy, 폴백 내장 |
| L3-b 벡터 인덱스 | ✅ | ❌ | 임베딩 API 필요 → 인터페이스만 |
| Agent 도구 6종 | 일부 | ✅ (도구 본체) | 도구는 순수 함수 — 테스트 가능 |
| Planner / Synthesizer | ✅ | ❌ | HCX-007 필요 → 프로바이더 + 스텁 |
| Verifier V1~V5 | ❌ | ✅ | 결정론 (LLM 미사용) |
| API 서버 + 계약 | ❌ | ✅ | 스텁 LLM으로 E2E 동작 |

**AC-0**: 키 없이 `GET /answer`가 **결정론 경로만으로** T1(단일 수치)·T3(비교)·T4(이벤트 집계)·T5(생애주기) 질의에 정답과 근거를 반환한다. Planner/Synthesizer 없이도 동작하는 **fast-path**를 갖는다.

> 이게 가능한 이유: 수치·집계·비교·이벤트 추적은 전부 SQL이다. LLM은 *문장 생성*에만 필요하고,
> 그 문장은 템플릿으로도 만들 수 있다. 즉 **LLM은 품질 향상 요소이지 정답 생성 주체가 아니다** (설계 D1).

---

## 1. 데이터 계약

### 1-1. 정규화 레코드 (파서 → 스토어 경계)

모든 파서는 아래 dataclass만 반환한다. DB를 직접 만지지 않는다.

```python
# src/dart_agent/models.py

@dataclass(frozen=True)
class DocMeta:
    doc_id: str            # {doc_group}_{rcept_no}
    corp_code: str         # 8자리 문자열
    corp_name: str
    doc_group: str         # periodic|major|exchange|holding
    doc_subtype: str | None
    report_nm: str
    rcept_no: str
    rcept_dt: str          # YYYYMMDD
    is_correction: bool
    base_year: int | None
    base_month: int | None
    file_path: str
    file_format: str       # xml|pdf+html

@dataclass(frozen=True)
class FinFact:
    doc_id: str
    corp_code: str
    acode: str | None      # ifrs-full_Revenue 등 (표 파싱 시 None)
    label_ko: str          # "매출액"
    metric_key: str | None # 정규화 지표키 (§1-3) — 조회 대상
    fy: int
    period_kind: str       # instant|duration
    basis: str             # consolidated|separate
    axis: str | None
    value_krw: int | None   # 🔴 항상 원 단위 정규화 (None = 정규화 실패)
    raw_value: str          # 원문 표기 그대로 ("300,870,903")
    raw_unit: str | None    # 원|천원|백만원|None
    unit_confidence: str    # high|low
    source: str            # xbrl|table
    src_section: str | None

@dataclass(frozen=True)
class Section:
    section_id: str        # {doc_id}#III-2-2
    doc_id: str
    corp_code: str
    path: str              # "III-2-2"
    title: str
    level: int
    text: str
    tables_md: str
    char_len: int
    content_class: str     # prose|table_registry|financial_stmt

@dataclass(frozen=True)
class ContractEvent:
    doc_id: str; corp_code: str
    event_kind: str        # 체결|해지|신규시설투자|투자판단관련
    contract_kind: str | None
    detail: str | None
    counterparty: str | None
    amount_krw: int | None
    recent_revenue_krw: int | None
    ratio_pct: float | None
    start_dt: str | None; end_dt: str | None; decision_dt: str | None

@dataclass(frozen=True)
class CapitalEvent:
    doc_id: str; corp_code: str
    event_kind: str        # 유상증자|전환사채(CB)|신주인수권부사채(BW)|교환사채(EB)|자기주식취득|…
    amount_krw: int | None
    decision_dt: str | None
    detail_json: str       # 유형별 가변 필드

@dataclass(frozen=True)
class HoldingEvent:
    doc_id: str; corp_code: str
    reporter: str | None
    cnt_before: int | None; rate_before: float | None
    cnt_after: int | None;  rate_after: float | None
    change_reason: str | None
    report_dt: str | None; prev_report_dt: str | None   # BFR_RPT_DT = 체인 포인터

@dataclass(frozen=True)
class CorrectionDiff:
    doc_id: str
    target_doc_kind: str | None   # "단일판매ㆍ공급계약 체결(자율공시)"
    target_submit_dt: str | None  # YYYYMMDD
    reason: str | None
    item: str | None
    before_val: str | None
    after_val: str | None

@dataclass(frozen=True)
class ParseResult:
    meta: DocMeta
    fin_facts: list[FinFact]
    sections: list[Section]
    contract_events: list[ContractEvent]
    capital_events: list[CapitalEvent]
    holding_events: list[HoldingEvent]
    corrections: list[CorrectionDiff]
    warnings: list[str]     # 🔴 침묵 실패 금지 — 부분 실패는 여기에 기록
```

### 1-2. 파서 인터페이스 (계약)

```python
class BaseParser(Protocol):
    doc_group: ClassVar[str]
    def parse(self, meta: DocMeta, corpus_root: Path) -> ParseResult: ...
```

**AC-P1** 모든 파서는 예외를 던지지 않는다. 실패는 `ParseResult.warnings`에 기록하고 부분 결과를 반환한다.
**AC-P2** 파서는 파일 시스템 읽기만 한다. DB·네트워크 접근 금지.
**AC-P3** 동일 입력 → 동일 출력 (결정론). 시간·랜덤 의존 금지.

### 1-3. 지표 정규화 키 (`metric_key`)

질의어 → 지표키 → ACODE/라벨 매핑. **단일 출처는 `src/dart_agent/metrics.py`**.

| metric_key | 질의 표현 | XBRL ACODE | 표 라벨 정규식 |
|---|---|---|---|
| `revenue` | 매출액, 매출, 영업수익, 매출총액 | `ifrs-full_Revenue`, `dart_Revenue` | `^매출액$\|^영업수익$` |
| `operating_income` | 영업이익 | `dart_OperatingIncomeLoss` | `^영업이익` |
| `net_income` | 당기순이익, 순이익 | `ifrs-full_ProfitLoss` | `당기순이익` |
| `total_assets` | 자산총계, 총자산 | `ifrs-full_Assets` | `^자산총계$` |
| `total_liabilities` | 부채총계 | `ifrs-full_Liabilities` | `^부채총계$` |
| `total_equity` | 자본총계 | `ifrs-full_Equity` | `^자본총계$` |
| `current_assets` | 유동자산 | `ifrs-full_CurrentAssets` | `유동자산` |
| `cash` | 현금및현금성자산 | `ifrs-full_CashAndCashEquivalents` | `현금.*현금성자산` |
| `ppe_acquisition` | 설비투자, 유형자산취득, CAPEX | `dart_PurchaseOfPropertyPlantAndEquipment` 등 | `유형자산.*취득` |
| `rnd_expense` | 연구개발비 | — | `연구개발비` |

**AC-M1** `metric_key` 해석은 **정규식 first-match**이며 순서가 명세된다 (동일 입력 → 동일 지표키).
**AC-M2** 미등록 표현은 `None`을 반환하고 abstention을 유발한다. **추측 금지**.

### 1-4. 단위 정규화 (Stage D)

```
scale: 원=1 · 천원=1_000 · 백만원=1_000_000 · 억원=100_000_000
value_krw = int(clean_number(raw_value)) * scale
clean_number: 콤마 제거 · "(123)" → -123 · "-" / "" / "－" → None
```

**AC-U1** 단위 미검출 시 `unit_confidence="low"`, `value_krw`는 **XBRL 교차검증 성공 시에만** 채운다.
**AC-U2** `unit_confidence="low"` fact는 `compare`/`rank` 연산에서 **제외**되고 그 사실이 응답에 고지된다.
**AC-U3** 음수 표기 `(11,526,297)` → `-11526297` (실측 확인된 DART 표기 규약).

---

## 2. 스토어 계약

### 2-1. 스키마

`src/dart_agent/store/schema.sql` — 제안서 §2-2 스키마를 그대로 구현. 추가 규칙:

**AC-S1** 모든 테이블에 `doc_id` FK. `document`가 없는 레코드는 적재 거부.
**AC-S2** `fin_fact` UNIQUE(doc_id, acode, label_ko, fy, period_kind, basis, axis) — 중복 적재 시 무시(idempotent).
**AC-S3** 적재는 단일 트랜잭션. 실패 시 롤백.
**AC-S4** `build_index.py`는 재실행 안전(idempotent). 동일 코퍼스 → 동일 DB 내용.

### 2-2. 별칭 테이블

**AC-A1** `universe.csv`의 `corp_name`/`listed_name`/`corp_eng_name`/`stock_code` 4종 + 수기 변형이 전부 등록된다.
**AC-A2** 별칭 → `corp_code`는 **1:1**. 충돌 시 빌드 실패(침묵 금지).
**AC-A3** 필수 수기 변형 (실측 확인된 통용명 불일치). 🔴 2026-08-19 정정: 아래 예시는 8종이나
실제 구현은 **24종**이다(`store/alias.py`) — 명세가 구현보다 뒤처져 있었다. 예시는 대표값이며
권위는 코드다:
```
현대차→현대자동차 · KT→케이티 · 엔씨소프트→NC · LIG넥스원→LIG디펜스앤에어로스페이스
JYP Ent.→JYP Ent · JYP→JYP Ent · 하이닉스→SK하이닉스 · 포스코→POSCO홀딩스
```
**AC-A4** 정규화는 공백·`(주)`·`주식회사`·대소문자 무시 후 매칭.

### 2-3. 정정 체인

**AC-C1** `exchange`/`major`/`periodic`: `CorrectionDiff.(target_doc_kind, target_submit_dt)` +
동일 `corp_code` → 원본 `rcept_no` 탐색. 매칭 규칙:
```
1) rcept_dt == target_submit_dt AND doc_group 동일 AND is_correction=False
2) 후보 2건+ → doc_subtype 문자열 유사도 최대
3) 후보 0건 → rcept_dt를 target_submit_dt ±3일로 확장 재시도
4) 여전히 0건 → unresolved 기록, is_effective=1 유지 (보수적)
```
**AC-C2** `holding`: `prev_report_dt`(BFR_RPT_DT) 명시 포인터 사용 — 근사 매칭 불필요.
**AC-C3** 체인 종단에 `is_effective=1`. 중간 노드는 0.
**AC-C4** `build_index.py`가 **매칭률을 표준출력에 보고**한다 (resolved/unresolved 건수).

---

## 3. 검색 계약

### 3-1. 한국어 BM25

**AC-R1** 토크나이저는 `kiwipiepy` 사용, 명사(NNG/NNP)+복합명사 분해. **미설치 시 문자 2-3gram으로 자동 폴백**하고 그 사실을 로그 1줄로 알린다.
**AC-R2** 금융 사용자 사전 등록: `연결기준 별도기준 유상증자 전환사채 신주인수권부사채 교환사채 자기주식 대량보유 설비투자 증감률` + 기업명 70종 + 섹터명 20종.
**AC-R3** BM25 파라미터 `k1=1.2, b=0.75`. 색인은 `prose` + `financial_stmt` 라벨 + `table_registry` 라벨만 (본문 제외).

### 3-2. 융합

**AC-R4** RRF `k=60`, `score = Σ 1/(60+rank)` (AITHOR `_rrf_fuse`와 동일).
**AC-R5** 벡터 랭킹은 `EmbeddingProvider`가 None이면 **건너뛰고** BM25 단독으로 동작한다 (키 없는 환경).

### 3-3. 섹션 주소 사전

**AC-R6** `src/dart_agent/retrieval/section_map.py`에 질의의도→path 매핑. 제안서 §2-3 10종 전체.

---

## 4. Agent 도구 계약 (순수 함수 — 테스트 대상)

```python
# 전부 순수 함수. LLM 없이 단독 호출·테스트 가능.
def fact_query(db, corp: list[str], metric: str, fy: list[int], basis: str|None) -> list[FactHit]
def get_section(db, corp: list[str], period: str, paths: list[str]) -> list[Section]
def doc_search(idx, query: str, **filters) -> list[SearchHit]
def event_query(db, corp: list[str], domain: str, kinds: list[str]|None, date_range) -> list[EventHit]
def trace_chain(db, doc_id: str|None, mode: str) -> ChainResult
def compute(op: str, operands: list[FactRef]) -> ComputeResult
```

**AC-T1** 모든 도구는 **결과에 출처(doc_id, section, raw_value)를 함께 반환**한다. 값만 반환하는 API 금지.
**AC-T2** `compute`의 operand는 `fact_id` 참조다. 숫자 리터럴 입력 금지 (D1 강제).
**AC-T3** `fact_query`는 `is_effective=1` 문서만 조회한다 (정정 반영).
**AC-T4** `compute(compare|rank)`는 `unit_confidence="low"` operand가 있으면 **거부하고 이유를 반환**한다.

---

## 5. 검증기 계약 (V1~V5, LLM 미사용)

```python
def verify(answer: str, ctx: EvidenceBundle, req: list[str]) -> VerifyReport
```

| ID | 규칙 | 실패 처리 |
|---|---|---|
| V1 | 답변의 모든 숫자가 (a) fact 치환 결과 (b) `retrieved_context` 내 존재 (c) 연도/질의 인용 중 하나 | 해당 문장 제거 + 재생성 요청 |
| V2 | 모든 `[Cn]`이 citation 목록에 존재 | 재생성 |
| V3 | `req[]` 각 항목에 대응 문장 존재 | 미충족 항목 명시 |
| V4 | 금지 표현 부재 — 목표주가·매수·매도·전망·예상·추천·유망 | 문장 제거 + 고지 |
| V5 | abstention 조건 판정 (§6) | 한계 고지 응답 전환 |

**AC-V1** V1~V5는 **정규식·집합 연산만** 사용한다. LLM 호출 0건.
**AC-V2** `verify()`는 순수 함수. 동일 입력 → 동일 판정.
**AC-V3** 숫자 추출은 한국어 수 표기 포함: `300,870,903` `300.9조` `2.32%` `12.86`.

---

## 6. Abstention 계약

```python
def decide_abstention(qspec, hits, search_top_score) -> Abstention | None
```

| 코드 | 조건 |
|---|---|
| `out_of_universe` | 대상 기업이 별칭 테이블에 없음 |
| `out_of_period` | 요청 기간 ∉ 2023-01 ~ 2026-03 |
| `no_evidence` | fact 미스 AND 검색 최고점 < τ |
| `unsupported_doctype` | 요청 정보가 보유 4유형에 없음 |
| `forbidden_prediction` | 미래 예측·투자 판단 요구 |
| `ambiguous` | 기업·기간·기준 미특정 → 역질문 |
| `low_unit_confidence` | 비교 질의인데 operand 단위 불확정 |

**AC-AB1** abstention 응답도 `answer` 필드에 **한국어 완결 문장**을 담는다. 빈 문자열 금지.
**AC-AB2** abstention 시 **확인 가능한 사실은 함께 제시**한다 (거부만 하고 끝내지 않음 — 평가지표 7).
**AC-AB3** `τ`는 설정값이며 기본 0.35, `config.py`에서 조정 가능.

---

## 7. API 계약

```
GET /answer?question_id={id}&question={q}
```

**AC-API1** 응답은 주최측 명시 5필드 `question_id`/`question`/`retrieved_context`/`think_trace`/`answer`를 **항상** 포함한다 (누락 시 실패).
**AC-API2** **HTTP 500을 반환하지 않는다.** 모든 내부 예외를 잡아 abstention 응답 + 200으로 변환.
**AC-API3** `question` 미제공 시 400 + 계약 형태 JSON.
**AC-API4** 타임아웃 `REQUEST_TIMEOUT_S`(기본 **120**) 초과 시 부분 근거 기반 응답.
LLM 보강 계층(서술·목차 라우팅)의 **예산 데드라인**으로 강제하며, 소진 시 결정론 템플릿으로
강등한다 — 결정론 경로가 정답 주체이므로 정확도 손실은 0이다.

> 🔴 2026-08-19 정정 2건 (AITHOR `resilience-audit` 지적 → 실측 확인):
> ① 이 상수는 **코드 어디서도 읽히지 않았다.** 배선되지 않아 재시도·페이싱이 누적되면
>    최악 지연이 질의당 680초 = 평가 타임아웃(300초)의 227%였다. 429가 나는 순간
>    정확도가 아니라 **타임아웃으로 0점**이 되는 경로였다.
> ② 기본값 25 → 120. 25초는 정상 서술을 자른다(실측 p95 27.1초·최대 65.4초).
>    120초는 관측된 성공을 보존하면서 평가 예산의 40%에 머문다.
**AC-API5** `GET /health` → `{"status":"ok"}` · `GET /ready` → 인덱스 로드 여부 · `GET /meta` → 코퍼스 통계.
**AC-API6** `question_id` 단위 응답 캐시 (동일 문항 재요청 시 재계산 없음).

---

## 8. LLM 프로바이더 계약

```python
class LLMProvider(Protocol):
    def chat(self, messages, tools=None, response_format=None) -> LLMResponse: ...

class EmbeddingProvider(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...
```

**AC-L1** `ClovaProvider`는 OpenAI 호환 엔드포인트 `https://clovastudio.stream.ntruss.com/v1/openai`를 사용한다.
**AC-L2** `CLOVA_API_KEY` 미설정 시 `StubProvider`로 자동 폴백하고 **경고 1줄**을 출력한다. 서버는 계속 동작한다.
**AC-L3** `StubProvider`는 결정론 템플릿 답변을 생성한다 (fast-path 검증용).
**AC-L4** 프로바이더 계층 밖에서 `requests`/`httpx` 직접 호출 금지.

---

## 9. 테스트 계약

**AC-TEST1** 파서 4종 각각 **실제 코퍼스 문서**를 픽스처로 쓰는 단위 테스트 (합성 데이터 금지).
**AC-TEST2** 인코딩 함정 회귀 테스트 — exchange 문서를 euc-kr로 읽으면 실패함을 명시적으로 검증.
**AC-TEST3** 단위 정규화 테스트 — 원/천원/백만원 + 음수 괄호 + 미검출.
**AC-TEST4** 별칭 테스트 — 통용명 8종 전부 정확한 `corp_code`로 해석.
**AC-TEST5** V1~V5 검증기 테스트 — 환각 숫자 주입 시 차단 확인.
**AC-TEST6** API 계약 테스트 — 5필드 존재 + 500 미발생 + abstention 경로.
**AC-TEST7** `pytest` 전체 통과가 완료 게이트다.

---

## 10. 완료 정의 (오늘 세션)

| # | AC | 검증 방법 |
|---|---|---|
| D1 | 파서 4종이 실제 문서를 파싱하고 warnings로 부분 실패 보고 | pytest + 실코퍼스 샘플 실행 |
| D2 | Fact Store 빌드 + 정정 매칭률 리포트 출력 | `build_index.py --limit N` 실행 |
| D3 | 별칭 8종 해석 성공 | pytest |
| D4 | 단위 정규화 (원/천원/백만원/음수/미검출) | pytest |
| D5 | BM25 검색 동작 (kiwi 또는 폴백) | pytest |
| D6 | 도구 6종 순수 함수 동작 | pytest |
| D7 | 검증기 V1~V5 동작 | pytest |
| D8 | `GET /answer` 5필드 반환 + 500 미발생 | pytest (TestClient) |
| D9 | 키 없이 T1 수치 질의 정답 반환 (fast-path) | 실제 DB로 E2E |
| D10 | `pytest` 전체 통과 | `python3 -m pytest` |

**미포함 (키 필요)**: 임베딩 인덱스 구축 · HCX Planner/Synthesizer 실호출 · Reranker · 전체 4,204건 인덱싱(시간).

---

## 11. 서술·라우팅 계층 계약 (LLM 사용 경로)

> 🔴 **2026-08-19 신설.** AITHOR `spec-architect` 검토에서 **scope creep 3건**이 확인됐다 —
> `agent/narrate.py`(270줄) · `agent/route_section.py`(130줄) · `llm/ratelimit.py`(107줄)이
> SPEC·TASKS·proposal 어디에도 언급 0건이었다. 그중 `narrate.py`는 **채점 대상인 `answer`
> 필드를 LLM이 직접 재작성**하는 계층이라 명세 부재의 대가가 크다.
> 조문 초안은 검토 에이전트가 작성했고, 실제 코드와 대조해 반영했다.

### 11-1. 서술 계층 (`agent/narrate.py`)

**AC-N1** `narrate()`는 이미 확정된 답변 본문만 입력받는다. 원자료·DB·검색 결과에
접근하지 않는다. LLM은 *다시 쓰기*만 하며 *조회·계산*은 하지 않는다 (D1 후반부).

**AC-N2** 서술본의 `(수치, 단위)` 쌍 집합은 원본과 완전 일치해야 채택된다.
정확한 값 뒤 괄호의 파생 환산 표기는 **누락만** 면제하며, 신규 생성은 면제하지 않는다.
> 근거: `17,569,457,486천원` → `…백만원` 변조가 자릿수 동일로 통과한 실사고.

**AC-N3** 서술본에 원본 본문·질문 어디에도 없는 내용어가 있으면 거부한다 (덧붙이기 금지).
> 근거: `"회계 감사에서 지적을 받았습니다"` 주입이 전 계층을 통과한 실측.

**AC-N4** 원본의 다음 요소가 서술본에서 **사라지면** 거부한다. 차집합이 아니라
**토큰별 존재 검사**로 구현한다 — 차집합은 삭제·반전을 원리적으로 검출하지 못한다.
- 기준어: `연결기준` `별도기준` `누적` `당기`
- 부정·미해결: `확인되지 않` `해당 없` `제공하지 않` `없습니다`
- 한계 고지: `범위를 벗어` `추가 정보가 필요`

**AC-N5** 원본에 없던 hedging(예상·추정·전망) 또는 편집 메타 주석이 서술본에
나타나면 거부한다.

**AC-N6** 강등이 발생하면 사유를 `think_trace`에 1줄 기록하고, 응답의
`degraded`/`degrade_reason` 필드에 노출하며, `confidence`를 한 단계 낮춘다.
**침묵 폴백 금지** — 조용한 저하는 장애 은폐다.

**AC-N7** LLM 전 계층을 차단한 상태에서 골드셋 점수가 유지되어야 한다.
LLM은 품질 향상 요소이지 정답 생성 주체가 아니다.

**AC-N12** 입력(본문·질문)에 지시문 패턴이 감지되면 **LLM을 호출하지 않는다**.
> 근거: 델리미터 + 시스템 지시를 넣고도 HCX가 주입 지시를 따른 실키 실측.
> 출력 가드로는 막을 수 없다 — 페이로드가 이미 비교 기준선(`body`)에 포함된다.

### 11-2. 목차 라우팅 (`agent/route_section.py`)

**AC-N8** LLM이 반환한 섹션 주소는 `CATALOG` 화이트리스트를 통과한 것만 사용한다.
카탈로그 밖 주소는 폐기하고 로그로 남긴다. 최대 2개.

**AC-N9** 규칙(`INTENT_PATHS`)이 주소를 찾은 질의에서는 LLM을 호출하지 않는다.

### 11-3. 레이트리밋 (`llm/ratelimit.py`, `api/ratelimit_mw.py`)

**AC-N10** CLOVA 429 수신 시 응답 헤더가 지시한 리셋 시간만큼 대기 후 재시도한다.
잔여 토큰이 임계 이하이면 선제 대기한다. 대기에는 **지터를 얹는다** — 고정 오프셋은
동시 요청을 결정론적으로 동기화한다.

**AC-N11** 재시도가 소진되면 결정론 템플릿으로 강등한다. 5xx 전파 금지 (AC-API2와 동일 원칙).

**AC-N13** 유입 제한: IP당 분당 `RATE_LIMIT_PER_MIN`(기본 60)회. 초과 시 429를 반환하되
**계약 5필드를 유지**한다. `Pacer`(자기 페이싱)와는 다른 층이다 — 전자는 우리가 HCX를
부르는 속도, 후자는 남이 우리를 부르는 속도를 다룬다.
