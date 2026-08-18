# 공시 Agent — 사전 조사 결과 (Evidence Base)

> 제10회 2026 미래에셋증권 AI Festival · 주제 「공시 Agent (Disclosure Analyst)」
> 작성일 2026-07-30 · 본 문서는 기술 명세서·MVP 제안서의 **근거 문서**다.
> 모든 수치는 실제 코퍼스/공식 문서에서 직접 측정·확인한 값이며, 추정치는 `[추정]`으로 표시한다.

---

## 1. 과제 요건 (주최측 자료 전수 추출)

출처: `docs/(배포용)과제소개자료_공시Agent.pdf` (9p)

### 1-1. 과제 정의

> 공시 데이터를 기반으로 기업 정보를 이해하고, 자연어 질의에 맞는 정보를 **검색·분석·설명**하는 공시 AI Agent.
> 단순 키워드 검색을 넘어, **여러 공시를 종합적으로 이해하고 비교·분석·설명**하는 Agent 구현이 핵심.

Agent에 요구된 3대 역할:

| # | 역할 | 세부 |
|---|---|---|
| 1 | 검색 및 정보 추출 | 질의에 적합한 공시 정확히 검색 / 사업·재무·투자·계약·자금조달·지분변동 등 정보 추출 |
| 2 | 종합 비교 및 연산 | 여러 공시 종합 → 연도별 변화·기업 간 비교 / 증감률·비중 등 **계산 기반 질의** / **변경·후속 공시를 연결한 변경 이력 분석** |
| 3 | 근거 기반 답변 | 공시 내용을 근거로 자연어 답변 / **환각 방지 — 공시에 없는 내용은 추측하지 않음** |

### 1-2. 하드 제약 (위반 시 실격 또는 평가 제외)

| 구분 | 제약 |
|---|---|
| 🔴 **필수** | LLM은 **HyperCLOVA X만** 사용 가능. 그 외 모델 사용 시 **평가대상에서 제외** |
| 🔴 금지 | 제공 코퍼스 외 데이터 사용 불가 (뉴스·리포트·위키 등) |
| 🔴 금지 | OpenDART 등 외부 공시 API 실시간 호출 불가 |
| 🔴 금지 | 공시에 근거 없는 미래 예측·투자 의견 생성 |
| 필수 | 답변은 제공 공시 데이터를 근거로 생성 |
| 필수 | 확인 불가 시 '확인할 수 없음 / 공시에서 확인되지 않음' 명시 |
| 필수 | **모든 답변에 근거 공시를 표시** |
| 자유 | 구현 범위/방식 제한 없음 — 전처리·구조화·임베딩·인덱싱·검색 방식 자유 (RAG · Vector DB · Re-ranker · GraphRAG · Agent Framework · Function Calling 등) |

> ⚠️ "LLM은 HyperCLOVA X만"의 해석 경계: **임베딩·리랭킹 모델이 LLM에 포함되는지 명시되어 있지 않다.**
> 안전 설계는 임베딩/리랭킹도 CLOVA Studio 제공 API(bge-m3 · Reranker)로 통일하는 것이다 (§3-3, 리스크 R1).

### 1-3. 평가 방식

**예선** — 정량 + 정성 종합평가로 본선 6팀 선발

- 정량: 참가팀이 준비한 **평가용 API End-point에 주최측이 GET 요청** → 비공개 평가문제에 대한 답변 호출 후 평가
  - 평가문제 구성: 난이도 **상/중/하 × Closed/Open-ended 혼합**
- 정성: 주제별 기술·도메인 전문가 검토
  - 항목: 문제정의 / 기술완성도·성능 / 창의성·확장성 / 답변의 정확성·완결성 / **협업 활용성·리스크 관리**

**정량 평가지표 7종** (참고용 질의 set에 명시):

| # | 지표 | 정의 |
|---|---|---|
| 1 | 정확성 | 답변의 수치, 사실 또는 비교(증감, 수치, 순위 등)가 정확한가 |
| 2 | 근거 완전성 | 답변 도출에 **필수적인 데이터를 검색 근거에 포함**했는가 |
| 3 | 요구사항 충족 | 질의에서 요구한 사항을 답변에 **누락 없이** 포함했는가 |
| 4 | 근거 기반 | Context에 없는 내용을 사실처럼 생성하지 않았는가 (Hallucination) |
| 5 | 추론 논리성 | 추론 및 답변 생성과정이 논리적인가 |
| 6 | 안전성 및 신뢰성 | 개인정보 노출, 부적절한 입출력, **프롬프트 공격**에 안전하게 대응하고 신뢰 가능한 서비스로서 답변 태도를 유지하는가 |
| 7 | 정보한계 대응 | 보유 데이터로 답변할 수 없는 질의를 식별하고, 무리한 답변 대신 **한계 고지 또는 필요한 정보를 역질문**으로 대응하는가 |
| — | 공통 | **모든 답변에는 근거 공시를 표시할 것** |

**본선/결선** — 10.01~10.16 멘토링 2~3회(네이버 1784 / 미래에셋증권 사옥, 대면) → 10월 중 결선 PT + **라이브 시연**, 참가팀 전원 대면참석 원칙.

### 1-4. 참고용 질의 set (6종) → 요구 능력 역산

| 유형 | 예시 질의 | 이 질의가 요구하는 실제 능력 |
|---|---|---|
| 검색·추출 / Closed | "OO기업의 2025년 연결기준 매출액은 얼마인가?" | 연결 vs 별도 구분 + 회계기간 특정 + **정확한 수치** |
| 검색·추출 / Open | "OO기업의 2026년 1분기 분기보고서를 기준으로 주요 투자 계획을 정리해줘" | 특정 문서 특정 섹션 지정 조회 + 서술 요약 |
| 다중조회·비교·연산 / Closed | "2차전지 기업 A와 B 중 2025년 설비투자 규모가 더 큰 기업은?" | **섹터 기반 기업 해석** + 동일 지표 정렬 + 비교 판정 |
| 다중조회·비교·연산 / Open | "OO기업이 2025년에 실시한 자금조달 내역을 유형별(유상증자, CB, BW, EB)로 정리해줘" | **주요사항보고서 유형별 집계** (이벤트 테이블 필요) |
| 복합 문서 추론 / Closed | "OO기업이 2025년에 체결한 주요 계약 이후 해지된 계약이 존재하는가?" | 거래소공시 **체결↔해지 이벤트 링크** |
| 복합 문서 추론 / Open | "OO기업의 2023년 사업보고서와 2025년 사업보고서를 비교했을 때 핵심 사업은 어떻게 변화했는지 설명해줘" | 동일 섹션 **시계열 diff** + 서술 대조 |

> **핵심 관찰**: 6종 중 4종이 **단일 문서 검색으로 풀리지 않는다**. 필요한 것은 벡터 검색이 아니라
> ① 지표 단위 정형 테이블, ② 이벤트 간 링크, ③ 섹션 단위 시계열 정렬이다. 아키텍처는 이 관찰에서 출발해야 한다.

### 1-5. 제출물 · 일정

| 항목 | 내용 |
|---|---|
| 제출 채널 | 주최측 제공 GitHub Organization 내 **Private Repository에 Push** (대용량은 클라우드 스토리지 링크) |
| 제출물 1 | 소스코드 + 재현 가능한 개발환경 정의 (Dockerfile, requirements.txt) + README.md (환경구성·실행 명령어) |
| 제출물 2 | 기술 제안서 — 제안 요약, 문제 정의, 제안 방법, **시스템 구성도**, 주요 기능 흐름도, 사용 시나리오, 기대효과·확장성 (자유 양식) |
| 제출물 3 | 평가용 API 서버 정보 — **End-point URL + API 명세서(요청/응답 JSON 스키마) 필수 명시** |
| 서버 환경 | NCP(네이버클라우드) 개설 또는 참가팀 선호 환경 자유. **단 Public 망 통신 가능 네트워크 필수** |
| 서버 운영 | **예선 평가기간 09.07 ~ 09.20 중 API 활성화 상시 유지** |
| 제출 마감 | **09.06** — 마감 이후 커밋·push·서버 배포 등 코드/결과물 변경 발견 시 **실격** |
| 크레딧 | NCP 자원 활용 시 **사용 크레딧 한도 초과 주의 — 초과 시 주최측 별도 비용보전 없음** |

**평가용 API 계약 (PDF 명시 스키마)**

```
# Request
GET https://{team-endpoint}/answer?question_id={id}&question={평가 질의}

# Response (JSON)
{
  "question_id":       "Q-001",
  "question":          "평가 질의 원문",
  "retrieved_context": "답변 생성에 참고한 검색 문서",
  "think_trace":       "사고 · 추론 · 도구 사용 과정",
  "answer":            "최종 생성 답변"
}
```

> `retrieved_context`와 `think_trace`가 **응답 스키마에 포함**되어 있다 = 평가지표 2(근거 완전성)와
> 5(추론 논리성)가 이 두 필드로 채점된다. 즉 **두 필드는 디버그 로그가 아니라 채점 대상 산출물**이다.

### 1-6. 남은 일정 (2026-07-30 기준)

| 이벤트 | 일자 | D-day |
|---|---|---|
| 오프라인 설명회 (네이버 그린팩토리, 각 팀 최소 1명 필참) | 08.06 | D+7 |
| **제출 마감** | **09.06** | **D+38** |
| 예선 평가 (API 상시 활성 유지) | 09.07~09.20 | — |
| 예선 결과 발표 | 10.01 | — |

> 실작업 가능 기간 **38일**. 이 제약이 MVP 범위 설계를 지배한다 (제안서 §범위 참조).

---

## 2. 코퍼스 실측 분석

경로: `docs/3.공시/corpus/` · 측정 방법: 실제 파일 파싱 (본 세션 직접 실행)

### 2-1. 전체 규모

| 항목 | 실측값 |
|---|---|
| 문서 수 | **4,204건** (manifest.jsonl 행 수) |
| XML 파일 수 | **4,616개** (사업보고서는 감사보고서 첨부 XML 별도 포함) |
| `list_*.json` (DART list API 원본) | 280개 |
| 원본 용량 | **5.3 GB** (periodic 5.0GB / holding 220MB / major 24MB / exchange 21MB) |
| 대상 기업 | 70개사 (KOSPI 61 / KOSDAQ 9), 업종 8 > 섹터 20 |
| 기간 | 2023-01-01 ~ 2026-03-31 |
| 기업당 문서수 | min 18 / median 50 / max 200 (셀트리온 200, 한화오션·대우건설 150) |

### 2-2. 문서 유형 구성

| doc_group | 건수 | 정정건수 | 세부 유형 |
|---|---:|---:|---|
| `periodic` 정기공시 | 1,054 | 159 | annual 291 · half 234 · quarter 529 |
| `major` 주요사항보고서 | 598 | 173 | 25종+ (자기주식처분 157, 자기주식취득 91, 상각형조건부자본증권 71, 유상증자 54, 신탁계약체결 47, 합병 23, **전환사채(CB) 22**, 분할 12, 주식교환·이전 10 …) |
| `exchange` 거래소공시 | 1,469 | **631 (43%)** | 단일판매공급계약체결 1,106 · 투자판단관련주요경영사항 300 · 신규시설투자등 43 · **공급계약해지 20** |
| `holding` 지분공시 | 1,083 | 41 | 주식등의대량보유상황보고서(5% 보고) |

> 🔴 exchange 정정 비율 **43%** — 정정 체인 처리는 옵션이 아니라 필수다. 미처리 시 같은 계약이
> 원본·정정본으로 중복 집계되어 "2025년 계약 총액" 류 질의에서 **체계적 과대계상**이 발생한다.

### 2-3. 포맷 실측 — **두 종류의 서로 다른 포맷이 섞여 있다**

| doc_group | 실제 포맷 | 인코딩 | 확인 방법 |
|---|---|---|---|
| `periodic` (1,466 xml) | **DART XML** (`dart4.xsd` / 감사보고서 `dart3.xsd`) | UTF-8 | 전수 스캔 — HTML 0건 |
| `major` (598) | **DART XML** (`dart4.xsd`) | UTF-8 | 전수 스캔 — HTML 0건 |
| `holding` (1,083) | **DART XML** (`dart4.xsd`) | UTF-8 | 전수 스캔 — HTML 0건 |
| `exchange` (1,469) | 🔴 **HTML** (`.xml` 확장자이나 `<html>` 문서) | 🔴 **선언 euc-kr, 실제 UTF-8** | 전수 스캔 — 1,469/1,469 HTML + 1,469건 euc-kr 선언 |

#### 🔴 함정 1 — exchange 인코딩 불일치 (실측 검증)

```python
b = open(f,'rb').read()
re.search(rb'charset=([\w-]+)', b).group(1)   # => b'euc-kr'   ← 선언
b.decode('euc-kr')   # => UnicodeDecodeError: illegal multibyte sequence (byte 0xeb)
b.decode('cp949')    # => UnicodeDecodeError
b.decode('utf-8')    # => OK. '1. 판매ㆍ공급계약 구분'
```

**meta 선언 charset을 신뢰하면 1,469건 전부 깨진다.** BeautifulSoup 기본 동작(meta 존중)·pandas
`read_html`·lxml `HTMLParser` 모두 이 함정에 빠진다. **바이트를 UTF-8로 강제 디코딩**해야 한다.

### 2-4. 🔵 핵심 발견 — 재무 수치는 XBRL 인라인 태깅되어 있다

정기공시 XML의 재무제표 테이블 셀에 `<TE>` (Tagged Element)가 삽입되어 있고, **IFRS/DART 표준
택소노미 코드**와 **회계기간·연결여부 컨텍스트**를 함께 보유한다.

삼성전자 2024 사업보고서(`20250311001085.xml`, 5.78M chars) 실측:

| 항목 | 값 |
|---|---|
| `<TE>` 총 개수 | **16,149** |
| distinct `ACODE` | **815종** |
| `ACONTEXT` 보유 `<TE>` (= 재무 fact) | **1,016** |
| distinct `ACONTEXT` | 72종 |

실제 추출 예:

```
ACODE = dart_OperatingIncomeLoss
ACONTEXT = CFY2024dFY_ifrs-full_ConsolidatedAndSeparateFinancialStatementsAxis_ifrs-full_ConsolidatedMember
value    = 32,725,961          # 2024 연결 영업이익 (백만원)

ACODE = dart_OperatingIncomeLoss
ACONTEXT = PFY2023dFY_..._ifrs-full_SeparateMember
value    = (11,526,297)        # 2023 별도 영업이익 — 괄호 = 음수
```

**`ACONTEXT` 문법 해독** (실측 72종 패턴에서 역산):

| 세그먼트 | 의미 | 관측값 |
|---|---|---|
| 기간 접두 | 당기 / 전기 / 전전기 | `CFY2024` / `PFY2023` / `BPFY2022` |
| 기간 성격 | 기말시점(instant) / 기중기간(duration) | `eFY` (재무상태표) / `dFY` (손익계산서) |
| 재무제표 축 | 연결 / 별도 | `ifrs-full_ConsolidatedMember` / `ifrs-full_SeparateMember` |
| 추가 축 | 자본 구성요소 (자본변동표) | `ifrs-full_ComponentsOfEquityAxis_ifrs-full_RetainedEarningsMember` 등 |

> ✅ **이것이 본 과제의 최대 레버리지다.** "2025년 **연결기준** 매출액"은 LLM이 표를 읽고 추측할
> 문제가 아니라 `(corp, year, ACODE=ifrs-full_Revenue, Consolidated)` 튜플 조회 문제다.
> 평가지표 1(정확성)·4(근거 기반)을 구조적으로 확보하는 경로다.

#### ⚠️ 단, 커버리지는 100%가 아니다 (실측)

`periodic` 문서를 유형별 무작위 25건씩 샘플링해 `<TE ... ACONTEXT=...>` 존재를 검사:

| doc_subtype | 표본 | ACONTEXT 태깅 有 | 태깅률 | `<TE>`는 있으나 ACONTEXT 無 |
|---|---:|---:|---:|---:|
| `annual` (291건) | 25 | 15 | **60%** | 10 |
| `half` (234건) | 25 | 7 | **28%** | 18 |
| `quarter` (529건) | 25 | 7 | **28%** | 18 |

> 🔴 **XBRL 단독 의존은 불가.** 반기·분기 보고서는 72%가 미태깅이다.
> → 표 파싱 폴백 경로가 **반드시** 필요하다 (기술 명세서 §L1-B).
> 표본 25건 기준이므로 ±10%p 오차 가능 `[추정]` — 전수 스캔은 인덱싱 파이프라인 1회차에 수행 예정.

### 2-5. 정기공시는 안정적 목차 계층을 갖는다

`<TITLE>` 태그가 표준 대분류(로마숫자)·중분류(아라비아) 체계를 그대로 보존한다. 삼성전자 2024
사업보고서에서 추출한 135개 `<TITLE>`:

```
I. 회사의 개요 → 1. 회사의 개요 / 2. 회사의 연혁 / 3. 자본금 변동사항 / 4. 주식의 총수 등 / 5. 정관에 관한 사항
II. 사업의 내용 → 1. 사업의 개요 / 2. 주요 제품 및 서비스 / 3. 원재료 및 생산설비 /
                  4. 매출 및 수주상황 / 5. 위험관리 및 파생거래 / 6. 주요계약 및 연구개발활동 / 7. 기타 참고사항
III. 재무에 관한 사항 → 1. 요약재무정보 / 2. 연결재무제표 / 2-1. 연결 재무상태표 / 2-2. 연결 손익계산서 /
                        2-3. 연결 포괄손익계산서 / 2-4. 연결 자본변동표 / 2-5. 연결 현금흐름표 /
                        3. 연결재무제표 주석 (1~33) / 4. 재무제표 / 4-1. 재무상태표 …
IV. 이사의 경영진단 및 분석의견
IX. 계열회사 등에 관한 사항
```

**교차 검증 (12개사 무작위 표본, 10개 섹터 횡단)** — 사업보고서 핵심 섹션 존재 여부:

| 기업 | `<TITLE>` 수 | I. 회사의 개요 | II. 사업의 내용 | III. 재무에 관한 사항 | II-1 사업의 개요 | III-1 요약재무정보 |
|---|---:|:---:|:---:|:---:|:---:|:---:|
| CJ제일제당 | 157 | ✅ | ✅ | ✅ | ✅ | ✅ |
| 우리금융지주 | 160 | ✅ | ✅ | ✅ | ✅ | ✅ |
| 두산퓨얼셀 | 64 | ✅ | ✅ | ✅ | ✅ | ✅ |
| SK텔레콤 | 152 | ✅ | ✅ | ✅ | ✅ | ✅ |
| 에스엠 | 157 | ✅ | ✅ | ✅ | ✅ | ✅ |
| 현대글로비스 | 141 | ✅ | ✅ | ✅ | ✅ | ✅ |
| 대우건설 | 150 | ✅ | ✅ | ✅ | ✅ | ✅ |
| 한전기술 | 156 | ✅ | ✅ | ✅ | ✅ | ✅ |
| 한미반도체 | 154 | ✅ | ✅ | ✅ | ✅ | ✅ |
| 한미약품 | 145 | ✅ | ✅ | ✅ | ✅ | ✅ |
| JYP Ent | 68 | ✅ | ✅ | ✅ | ✅ | ✅ |
| 우리금융지주(타기수) | 70 | ✅ | ✅ | ✅ | ✅ | ✅ |

> **12/12 전부 5개 핵심 섹션 보유** (금융·소비재·에너지·통신·엔터·물류·건설·원전·반도체·제약 횡단).
> `<TITLE>` 총수는 64~160으로 변동한다 — 이는 주석 하위 항목 수 차이이며 **상위 목차 골격은 불변**이다.
> → 섹션 주소 지정 설계의 전제가 검증되었다.

> ✅ 이 목차는 **법정 서식이라 70개사 전부에서 동일**하다. 따라서
> ① "2026년 1분기 보고서의 **주요 투자 계획**" → `II-3. 원재료 및 생산설비` + `II-4. 매출 및 수주상황` 결정론적 주소 지정,
> ② "2023 vs 2025 **핵심 사업** 변화" → 양쪽 `II-1. 사업의 개요` 섹션 정렬 후 대조
> 가 가능하다. 벡터 검색 없이 **섹션 주소 기반 조회**로 참고 질의 2종을 직접 처리한다.

### 2-6. 볼륨은 극단적으로 편중되어 있다 (인덱싱 비용의 핵심)

표본 30건/유형, 태그 제거 후 순수 텍스트 길이 실측:

| doc_group | 건수 | 평균 원본 | 평균 텍스트 | 텍스트 총량 | 비중 |
|---|---:|---:|---:|---:|---:|
| `periodic` | 1,054 | 4.34 MB | 361.0K chars | **380.5M chars** | **95.0%** |
| `holding` | 1,083 | 0.19 MB | 13.0K chars | 14.1M chars | 3.5% |
| `exchange` | 1,469 | 0.01 MB | 1.9K chars | 2.8M chars | 0.7% |
| `major` | 598 | 0.03 MB | 3.7K chars | 2.2M chars | 0.6% |
| **합계** | 4,204 | — | — | **≈ 400M chars** | 100% |

- 토큰 환산 **≈ 2.2억 ~ 3.6억 토큰** `[추정]` (한국어 1.1~1.8 char/token)
- 1,000자 균등 청킹 시 **≈ 40만 청크** — 전량 임베딩은 비용·시간 모두 비현실적

#### 정기공시 내부 볼륨 분포 (삼성전자 2024 사업보고서, 660K chars / 135 섹션)

| 비중 | 섹션 | 성격 |
|---:|---|---|
| 15.6% | 1. 임원 및 직원 등의 현황 | 인명 레지스트리 표 |
| 8.6% | 4. 연구개발실적(상세) | 표 |
| 6.5% | IX. 계열회사 등에 관한 사항 | 표 |
| 4.9% | 30. 특수관계자와의 거래 | 표 |
| 4.5% | 1. 연결대상 종속회사 현황(상세) | 표 |
| 4.1% | 2. 계열회사 현황(상세) | 표 |
| 2.9% | 8. 기타 재무에 관한 사항 | 표 |

> ✅ **상위 7개 섹션(≈47%)이 전부 레지스트리성 표다.** 이들은 의미 임베딩의 대상이 아니라
> 정형 테이블로 적재해 SQL 조회하는 것이 정확도·비용 양쪽에서 우월하다.
> "표는 구조화, 서술은 임베딩" 분리 정책으로 **임베딩 대상 볼륨 ≈ 절반으로 축소** 가능.

### 2-6-bis. 🔴 금액 단위가 문서마다 다르다 (비교 질의의 함정)

`III-1 요약재무정보`는 XBRL 미태깅 문서의 폴백 앵커로 쓸 수 있을 만큼 규칙적이다.
미태깅 분기보고서(레인보우로보틱스 2024.03) 실측 추출:

```
요약재무정보 > 가. 요약연결재무정보   (단위:천원)          ← 단위 선언
구분              | 제14기(2024년 3월말) | 제13기(2023년 12월말) | 제12기(2022년 12월말)
[유동자산]         | 120,718,769        | 121,223,138         | 62,092,470
 - 당좌자산        | 113,309,973        | 115,046,489         | 56,981,999
 - 재고자산        |   7,408,796        |   6,176,649         |  5,110,471
[비유동자산]       |  13,848,597        |  11,388,350         | 14,066,998
자산총계           | 134,567,366        | 132,611,487         | 76,159,468
부채총계           |   3,237,353        |   1,035,403+...     | ...
```

✅ 폴백 가능성 확인 — 헤더가 `제N기(YYYY년 M월말)` 형식이라 **기수 ↔ 회계기간 매핑도 도출된다.**

🔴 **그러나 단위가 문서마다 다르다.** 정기공시 60건 표본에서 `요약재무정보` 인접 단위 선언:

| 선언 단위 | 건수 |
|---|---:|
| 백만원 | 32 |
| 원 | 12 |
| **미검출** (인접 12,000자 내 단위 표기 없음) | 16 |
| 천원 | 별도 확인 (레인보우로보틱스) |

> 🔴 **최소 3종 스케일(원 / 천원 / 백만원)이 혼용되며, 표본 27%는 인접 범위에 단위 표기가 없다.**
> 단위 정규화 없이 「A사 vs B사 설비투자 비교」를 수행하면 **1,000배 오차**가 발생한다.
> 대응: ① 단위 선언 파싱 → 전 값을 `원` 기준으로 정규화 저장,
> ② 미검출 시 XBRL 값 또는 `universe.market_cap`과 자릿수 정합성 교차검증,
> ③ 그래도 불확정이면 해당 fact를 `unit_confidence=low`로 표시하고 비교 질의에서 제외 + 한계 고지.

### 2-7. 수시·지분공시는 완전 정형이다

#### `holding` (지분공시) — `<TE ACODE>`로 전 필드 태깅

CJ제일제당 5% 보고 실측 추출:

| ACODE | 값 | 의미 |
|---|---|---|
| `RPT_RSP_NM` | 국민연금공단 | 보고자 |
| `CRP_NM` / `CRP_CD` | CJ제일제당(주) / 097950 | 대상 법인 |
| `SUM_BMT_CNT` / `SUM_BMT_RT` | 1,936,407 / **12.86** | 변동 **전** 보유주식수 / 비율 |
| `SUM_TMT_CNT` / `SUM_TMT_RT` | 1,783,406 / **11.85** | 변동 **후** 보유주식수 / 비율 |
| `SUM_CHN_RWN` | 단순추가취득/처분 | 변동 사유 |
| `BFR_RPT_DT` / `THS_RPT_DT` | 20220422 / 20230119 | 직전 보고일 / 금회 보고일 |

> `BFR_RPT_DT`가 **직전 보고서를 가리키는 명시적 포인터**다 → 지분 변동 이력 체인을 결정론적으로 복원 가능.

#### `exchange` (거래소공시) — 규칙적 key-value 표

현대건설 공급계약 정정본 실측 추출:

```
정정일자                     | 2023-01-10
1. 정정관련 공시서류          | 단일판매ㆍ공급계약 체결(자율공시)   ← 원본 문서 유형
2. 정정관련 공시서류제출일     | 2022-09-16                        ← 원본 접수일
3. 정정사유                  | 계약기간 변경
4. 정정사항  정정항목 | 정정전 | 정정후                            ← before/after diff 표
5. 계약기간  종료일  | 2025-06-13 | 2025-08-29
---
1. 판매ㆍ공급계약 구분        | 공사수주
   세부내용                  | 홍콩 유나이티드 크리스천 병원공사 계약체결
2. 계약내역  계약금액(원)      | 393,815,880,000
            최근매출액(원)     | 16,970,859,431,638
            매출액대비(%)      | 2.32
            대규모법인여부      | 해당
3. 계약상대                  | 홍콩 병원관리국(Hospital Authority)
```

> ✅ 정정공시가 **`(원본 문서유형, 원본 제출일)` 포인터 + 항목별 정정전/정정후 표**를 스스로 담고 있다.
> → `(corp_name, 문서유형, 제출일)` 조인으로 원본 `rcept_no`를 복원해 **정정 체인 그래프**를 구성할 수 있고,
> "변경 이력 분석"(과제 역할 2)과 참고 질의 "체결 이후 해지된 계약" 을 결정론적으로 처리한다.

### 2-8. 조인 키 함정 (README 명시 + 실측)

| 함정 | 내용 |
|---|---|
| 선행 0 유실 | `corp_code`(8자리)·`stock_code`(6자리)는 **문자열**. `dtype={'corp_code':str,'stock_code':str}` 필수 |
| 통용명 ≠ 법인명 | 조인 키는 `corp_name`(DART 공식 법인명) = `raw/` 하위 폴더명. 현대차→**현대자동차**, KT→**케이티**, 엔씨소프트→**NC**, LIG넥스원→**LIG디펜스앤에어로스페이스**(2026-04 사명변경), JYP Ent.→**`JYP Ent`**(폴더명 제약) |
| 건수 0 ≠ 결측 | 정기공시는 상장 이후분만 존재(시프트업 2024-07 상장 → 7건). 수시·거래소공시는 이벤트 없으면 0건 |
| 대체 수집 3건 | `file_format=pdf+html` — 한화에어로스페이스 분기(2026.03), KB금융 [기재정정]사업(2025.12), 한화오션 [기재정정]분기(2024.03). XML 파서로 처리 불가 |

> 🔴 사용자 질의는 **통용명**("현대차 매출")으로 들어온다. 별칭 정규화 사전 없이는 조회 실패한다.
> `universe.csv`의 `listed_name` ↔ `corp_name` ↔ `corp_eng_name` ↔ `stock_code` 4-way 별칭 테이블이 필수다.

---

## 3. HyperCLOVA X / CLOVA Studio 플랫폼 능력 조사

출처: NCP 공식 문서 (`guide.ncloud-docs.com`, `api.ncloud-docs.com`) 2026-07-30 조회

### 3-1. 모델 스펙

| 모델 | Context | Max Output | Function Calling | Structured Output | Thinking | 입력 |
|---|---:|---:|:---:|:---:|:---:|---|
| **HCX-007** | **128,000** | **32,768** | ✅ | ✅ | ✅ (hybrid) | Text |
| HCX-005 | 128,000 | 4,096 | ✅ | ❌ | ❌ | Text/Image |
| HCX-DASH-002 | 32,000 | 4,096 | ✅ | ❌ | ❌ | Text |
| HCX-003 | 8,192 | 4,096 | ❌ | ❌ | ❌ | Text |
| HCX-DASH-001 | 4,096 | 4,096 | ❌ | ❌ | ❌ | Text |

- 튜닝 지원: HCX-005 / DASH-002 / 003 / DASH-001 — **HCX-007은 튜닝 미지원**
- ✅ **HCX-007이 Function Calling + Structured Output + Thinking을 모두 지원** → Agent 오케스트레이션의 기반 모델로 확정
- 128K 컨텍스트는 넉넉하지만, 정기공시 1건 평균 텍스트가 361K chars(≈20만~33만 토큰)이므로 **문서 통째 투입은 불가**. 검색·섹션 선별이 여전히 필수

### 3-2. 부가 API — RAG 구성요소가 플랫폼에 내장되어 있다

Base URL: `https://clovastudio.stream.ntruss.com` · 인증: `Authorization: Bearer nv-***`

| API | 경로 | 용도 |
|---|---|---|
| Chat Completions v3 | `/v3/chat-completions/{model}` | thinking / function calling / structured outputs |
| **Reranker** | `POST /v1/api-tools/reranker` | 질의-문서 관련성 평가 및 선별 (입력 128K / 출력 4,096) |
| **RAG Reasoning** | `POST /v1/api-tools/rag-reasoning` | 근거 기반 답변 + **`<doc-ID>content</doc-ID>` 인용 마커** 생성 |
| Embedding v2 | `/v1/api-tools/embedding/v2` | bge-m3 |
| Embedding v1 | `/v1/api-tools/embedding/...` | clir-emb-dolphin / clir-sts-dolphin |
| Segmentation (문단 나누기) | `/v1/api-tools/segmentation` | 문장 유사도 기반 주제 단위 분할 (`segCnt`/`segMaxSize`/`segMinSize`) |
| Summarization | `/v1/api-tools/summarization` | 구간 요약 |
| Token Calculator | `/v1/api-tools/tokenizer...` | chat / chat v3 / embedding v2 별 토큰 계산 |
| Sliding Window | `/v1/api-tools/sliding` | 최대 토큰 초과 시 오래된 turn 삭제 (v3 미지원) |
| Router / Skillset | `/v1/...router`, `...generateskillsetfinalanswer` | 질의 라우팅 / 스킬셋 |

**임베딩 모델 스펙**

| 모델 | Max Tokens | 차원 | 거리 |
|---|---:|---:|---|
| clir-emb-dolphin | 500 | 1024 | Inner Product |
| clir-sts-dolphin | 500 | 1024 | Cosine |
| **bge-m3 (v2)** | **8,192** | 1024 | Cosine |

> ✅ bge-m3(8,192 토큰)를 써야 한다. clir 계열 500토큰 한계는 공시 청크에 부적합.
> ✅ **Reranker가 플랫폼 제공**이므로 외부 리랭커(Cohere/BGE) 도입 없이 "HyperCLOVA X만" 제약을 지키며 2-stage 검색 구성 가능.

**Reranker 요청 스키마 (공식)**

```json
{ "query": "질의",
  "documents": [ {"id": "doc-1", "doc": "본문"}, ... ],
  "maxTokens": 4096 }
```
응답: `result.citedDocuments[] {id, doc}` · `result.suggestedQueries[]` · `result.usage`

**RAG Reasoning 동작 (공식)** — Function Calling 형태로 동작한다

```
1) 사용자 질의 + tools 정의 전달
2) 모델이 toolCalls로 검색 함수 호출
3) 클라이언트가 문서 ID + 본문 회수
4) role:"tool" 메시지로 검색 결과 반환
5) 모델이 <doc-ID>...</doc-ID> 인용 마커를 포함한 최종 답변 생성
```
파라미터: `maxTokens` 1024~4096 · `topP` ≤1.0 · `topK` ≤128 · `temperature` ≤1.0 · `repetitionPenalty` ≤2.0 · `seed` · `includeAiFilters`
한계: 입력 128K / **출력 4,096**

### 3-3. 🔵 OpenAI 호환 엔드포인트가 존재한다

```
Base URL: https://clovastudio.stream.ntruss.com/v1/openai/
  POST /chat/completions     (v3 포함)
  POST /embeddings           (v2 포함)
  GET  /models
```

| 항목 | 내용 |
|---|---|
| 필드 규약 | snake_case (native는 camelCase) |
| 지원 파라미터 | `messages` `model` `stream` `max_completion_tokens` `temperature` **`tools`** **`tool_choice`** **`response_format`** `top_p` `stop` |
| 미지원 | `audio` `frequency_penalty` `logit_bias` `modalities` `presence_penalty` `user` |
| CLOVA 전용 | `top_k` `repeat_penalty` `repetition_penalty` |
| 제약 | `n`은 1만 · `encoding_format`은 `float`만 (base64 미지원) · `dimensions` 기본 1024 |
| 인증 | **CLOVA Studio API 키** (OpenAI 키 아님) |

> ✅ **엔지니어링 속도의 결정적 요인.** OpenAI SDK / LangChain / LlamaIndex를 그대로 쓰면서
> LLM은 HyperCLOVA X만 사용하는 상태를 만족한다. 자체 HTTP 클라이언트 작성 불필요.

### 3-4. 미확인 항목 (설명회에서 반드시 확인)

| # | 미확인 사항 | 왜 중요한가 |
|---|---|---|
| U1 | RPM / TPM / 동시요청 한도 | 40만 청크 임베딩 배치 소요시간·평가기간 동시성 설계가 이 값에 종속. 공식 문서에 미기재 |
| U2 | 제공 크레딧 규모 및 과금 단가 | 임베딩 총량(≈1억 토큰 `[추정]`)이 크레딧 내인지 판단 불가. 초과 시 자기 부담 명시됨 |
| U3 | 임베딩·리랭커가 "LLM" 제약에 포함되는지 | 포함이면 bge-m3/Reranker도 CLOVA 것만 허용 → 로컬 임베딩 모델 전면 금지 |
| U4 | 평가 시 동시 요청 수 / 타임아웃 허용치 | GET 1건당 응답시간 예산(SLO) 설계 근거 |
| U5 | `question_id` 재요청·재현성 요구 여부 | 캐싱 전략 및 `seed` 고정 정책 |

---

## 4. 재사용 자산 인벤토리

### 4-1. AITHOR-Agent-Framework (`../AITHOR-Agent-Framework`)

| 항목 | 실측 |
|---|---|
| 규모 | 691 파일 / ≈35K LOC Python |
| LLM 프로바이더 | `src/aithor_agent_framework/llm_providers.py` — OpenAI / Anthropic / Gemini / OpenRouter (urllib 기반, stdlib only, 키 자동 마스킹, 재시도 내장) |
| 🔴 HyperCLOVA X 프로바이더 | **없음** |
| RAG | `rag.py` — `HybridRetriever` (BM25 + token-vector + optional semantic), `SQLiteRAGBackend` |
| 평가 | faithfulness / answer-relevance 스코어링 |
| 🔴 한국어 형태소 토크나이저 | **없음** |

**결정적 확인 — 프로바이더 추가는 소규모 작업이다** (실제 코드 확인):

```python
# llm_providers.py:414
class OpenAIProvider:
    def __init__(self, ..., base_url: str = "https://api.openai.com/v1", ...):
        self.base_url = base_url.rstrip("/")
    # :525, :588  →  f"{self.base_url}/chat/completions"

# llm_providers.py:628  ← 이미 base_url만 바꿔 상속하는 선례가 존재
class OpenRouterProvider(OpenAIProvider):
    def __init__(self, ..., base_url: str = "https://openrouter.ai/api/v1", ...):
```

> ✅ `base_url`이 생성자 인자이고 `OpenRouterProvider`가 **동일 패턴의 선례**다.
> `ClovaProvider(OpenAIProvider)` with `base_url="https://clovastudio.stream.ntruss.com/v1/openai"` = 수십 줄 서브클래스.
> `tools` / `tool_choice` / `response_format` 모두 CLOVA 호환 계층이 지원하므로 Function Calling 경로도 그대로 동작한다 `[추정 — 실호출 미검증]`.

### 4-2. AI-research-SKILLs (`../AI-research-SKILLs`, 78 도메인)

| 스킬 | 보유 기법 (실측 확인) | 본 과제 가치 |
|---|---|---|
| 🔵 **`15-rag/pageindex/SKILL.md`** (141줄) | **Vectorless RAG** — 계층형 트리 인덱싱 + LLM 트리 탐색. 임베딩·청킹·벡터DB 불요. **FinanceBench 98.7%** (vs 벡터 RAG 80~90%). 명시 적용 대상: *"문서 구조(목차·섹션 계층)가 중요한 금융/법률/기술 문서"* | **🔴 CRITICAL** — 본 설계 D2(주소 우선)의 **외부 벤치마크 검증**. §5 P3 참조 |
| `15-rag/SKILL.md` (654줄) + 30개 하위 스킬 | 벡터DB 11종(pgvector/qdrant/chroma/faiss/milvus/lancedb/pinecone/weaviate/turbovec…), 리랭킹(cohere-rerank), RAGAS, GraphRAG 계열(lightrag/llm-graph-builder/kggen/openkb), `unstructured`(문서 ETL), `hyper-extract`(타입 지정 구조화 추출) | **HIGH** — 검색 스택 설계 근거 |
| `41-on-device-hybrid-search/SKILL.md` (492줄) | QMD 패턴 (BM25 + sqlite-vec + Reranker), **RRF 융합**(RRF 5회·reciprocal rank 언급), 클라우드 API 불요 | **HIGH** — 단일 서버 하이브리드 검색 레시피가 본 배포 형태와 정합 |
| `15-rag/adaptive-chunking/SKILL.md` (135줄) | Ekimetrics, LREC 2026 / arXiv:2603.25333. **문서별 청킹 전략 선택** + 정답 없이 청크 품질 채점하는 5개 intrinsic metric (Size Compliance, Intrachunk Cohesion, Document Contextual Coherence, Block Integrity, Reference Completeness) | **HIGH** — 본 코퍼스는 4종 포맷 이질 혼재 → 단일 splitter 부적합. 정답 없는 품질 채점이 특히 유용 |
| `11-evaluation/SKILL.md` (1,012줄) | RAGAS · faithfulness · hallucination 언급 + 평가 harness 8종(lm-evaluation-harness/nemo-evaluator/bineval…) | **MEDIUM** — Gold Set 채점 러너 |
| `16-prompt-engineering/instructor/` (535줄 부모 + 하위) | JSON Schema 구조화 출력 검증 (instructor 13회 언급), guardrails-ai, dspy, guidance | **MEDIUM** — HCX-007 Structured Output과 결합 |
| `17-observability/SKILL.md` (455줄) | langsmith/langfuse/phoenix/opik/helicone/opentelemetry-llm | **MEDIUM** — 크레딧 소진 감시 |
| `15-rag/unstructured/` (242줄) | PDF/DOCX/HTML 등 40+ 포맷 문서 ETL, layout-aware ML 추출 | **LOW** — 본 코퍼스는 DART 전용 파서 필요. `pdf+html` 3건에만 유용 |
| ⚠️ `76-finance-agent-skills/SKILL.md` (115줄) | `himself65/finance-skills` **레퍼런스 가이드** — DCF 밸류에이션, 실적 프리뷰, 소셜 센티먼트(Twitter/Discord 리더), 옵션 페이오프, TradingView/Hyperliquid. 데이터원: yfinance/opencli/tdl | **🔴 무관 + 제약 충돌** — 투자 분석·트레이딩 스킬 팩이며 공시 검색과 무관. 게다가 **외부 데이터 의존이 본 과제 금지 조항과 정면 충돌**. 사용 금지 |
| `25-backend-architect` | Express/Prisma/Redis 백엔드 패턴 | **LOW** (Python 스택 채택 시) |

#### 🔵 PageIndex가 본 설계에 주는 의미 (중요)

PageIndex(VectifyAI, MIT, 25.4k★)는 우리가 독립적으로 도달한 **D2 "주소 우선, 검색은 나중"** 원칙과
동일한 접근이며, **금융 문서 벤치마크에서 정량 검증**되어 있다.

| 항목 | 벡터 RAG | PageIndex |
|---|---|---|
| 검색 방식 | 코사인 유사도로 "비슷한" 청크 | LLM 추론으로 "관련 있는" 섹션 |
| 문서 구조 | 청킹으로 파괴 | 계층 트리 보존 |
| FinanceBench 정확도 | ~80-90% | **98.7%** |
| 설명 가능성 | 유사도 점수만 | **페이지/섹션 단위 추적** |

> ✅ **그리고 우리는 PageIndex의 유일한 약점을 회피한다.**
> PageIndex의 주 비용은 **Stage 1 인덱싱을 LLM으로 수행**하는 것이다 (SKILL.md "Common Issues: 인덱싱 비용이 높음",
> "잘 구조화된 PDF에서 최적 성능", "Markdown 변환이 원본 계층을 완벽히 보존하지 못할 수 있음").
> **본 코퍼스는 트리를 이미 갖고 있다** — `<TITLE>` 태그가 법정 목차 계층을 그대로 보존하므로(§2-5)
> 트리 구축이 **LLM 없이 결정론적 파싱**으로 끝난다. 즉 PageIndex의 정확도 이점은 취하고 비용 단점은 제거한다.
> 이는 R2(크레딧 초과) 완화에도 직접 기여한다.
>
> ⚠️ 단 PageIndex 라이브러리를 그대로 쓰지는 않는다 — PDF 입력 전제이고 `litellm` 의존이라
> HyperCLOVA X 전용 제약과 어긋날 수 있다. **아키텍처 사상만 차용**하고 트리는 자체 구축한다.
> FinanceBench 98.7%는 **영문 재무문서 + 타 모델(Mafin 2.5) 기준**이므로 한국어 DART + HCX-007에
> 그대로 전이된다고 볼 수 없다 `[추정]`.

**🔴 확인된 공백**: `MeCab` / `Kiwipiepy` / `KoNLPy` / `Nori` 언급 **0건**.
한국어 BM25 형태소 분석은 두 저장소 어디에도 없다 → **직접 통합해야 하는 유일한 코어 미보유 요소**.

> ⚠️ 재사용 판단 주의: AITHOR는 범용 프레임워크이고 본 과제는 단일 도메인 파이프라인이다.
> 프레임워크 전체를 끌어오면 38일 일정에서 학습·적응 비용이 이득을 초과할 수 있다.
> **선별 차용**(프로바이더 패턴 · HybridRetriever · faithfulness 스코어러)이 합리적이다.

---

## 5. 조사가 도출한 아키텍처 명제

| # | 명제 | 근거 |
|---|---|---|
| P1 | **숫자는 LLM이 생성하지 않는다.** XBRL 태그·정형 필드에서 결정론적으로 조회해 주입한다 | §2-4, §2-7 — 재무·계약·지분 수치 전부 태깅되어 있음 |
| P2 | XBRL 단독은 불가. **표 파싱 폴백**이 필수다 (앵커 = `III-1 요약재무정보`) | §2-4 태깅률 28%, §2-6-bis 폴백 가능성 확인 |
| P2b | **금액 단위를 `원`으로 정규화**하고, 불확정 값은 비교에서 배제한다 | §2-6-bis — 원/천원/백만원 혼용, 27% 미표기 |
| P3 | 정기공시는 **법정 목차 주소로 직접 조회**한다. 벡터 검색은 그 다음이다 | §2-5 목차 12/12 교차검증 + §4-2 PageIndex FinanceBench 98.7% 외부 검증 |
| P4 | **레지스트리성 표는 임베딩하지 않는다.** 정형 적재 후 SQL | §2-6 — 상위 7개 섹션 47%가 표 |
| P5 | **정정 체인은 1급 시민이다.** 미처리 시 집계 과대계상 | §2-2 — exchange 정정 43%, §2-7 포인터 존재 |
| P6 | 기업 **별칭 정규화 사전**이 조회 성공의 전제조건이다 | §2-8 — 통용명 ≠ 법인명 |
| P7 | 리랭킹·임베딩은 **CLOVA Studio 제공 API로 통일**한다 (제약 해석 안전판) | §1-2, §3-2 |
| P8 | `retrieved_context`·`think_trace`는 **채점 대상 산출물**로 설계한다 | §1-5 — 응답 스키마에 포함 |
| P9 | OpenAI 호환 계층으로 **표준 생태계를 그대로 사용**한다 | §3-3 |
| P10 | 한국어 형태소 BM25는 **유일한 자체 개발 코어 요소**다 | §4-2 |

---

## 검증 수준

| 핵심 주장 | 수준 | 근거 |
|---|---|---|
| 과제 제약·평가지표·제출 스키마·일정 | [검증됨] | 주최측 PDF 9페이지 전면 판독 |
| 코퍼스 4,204문서 / 5.3GB / 4,616 XML / 유형별 건수 | [검증됨] | manifest.jsonl 파싱 + `du`/`find` 실측 |
| exchange 1,469건 전부 HTML이며 meta는 euc-kr, 실제 UTF-8 | [검증됨] | 전수 스캔 + 3개 인코딩 디코딩 시도 (euc-kr/cp949 실패, utf-8 성공) |
| periodic/major/holding은 DART XML, HTML 0건 | [검증됨] | 전수 스캔 |
| `<TE ACODE/ACONTEXT>` XBRL 인라인 태깅 존재 및 문법 | [검증됨] | 삼성전자 사업보고서에서 실제 값 추출 (영업이익 연결/별도 대조 확인) |
| XBRL 태깅률 annual 60% / half 28% / quarter 28% | [추정] | 유형별 무작위 25건 표본 — 표본오차 ±10%p 수준, 전수 미검증 |
| 정기공시 법정 목차 구조 안정성 (핵심 5섹션) | [검증됨] | 12개사 무작위 표본(10섹터 횡단) 전수 보유 확인. `<TITLE>` 총수는 64~160 변동하나 상위 골격 불변 |
| 텍스트 총량 ≈400M chars, periodic 95% 편중 | [추정] | 유형별 30건 표본 평균 × 전체 건수 외삽 |
| 섹션 볼륨 상위 7개 ≈47% 레지스트리 표 | [검증됨/1건] | 삼성전자 1건 실측. 타 기업 일반화는 [추정] |
| `III-1 요약재무정보`가 미태깅 문서의 폴백 앵커로 파싱 가능 | [검증됨] | 레인보우로보틱스 미태깅 분기보고서에서 행·기수 매트릭스 실제 추출 |
| 금액 단위 원/천원/백만원 혼용 + 27% 인접 미표기 | [검증됨] | 60건 표본 정규식 실측 (백만원 32 / 원 12 / 미검출 16) + 천원 개별 확인 |
| 정정공시가 원본 포인터 + 정정전/후 diff 표 보유 | [검증됨] | 현대건설 정정본 실제 추출 |
| holding 전 필드 `<TE ACODE>` 태깅 + `BFR_RPT_DT` 포인터 | [검증됨] | CJ제일제당 5% 보고 실제 추출 |
| HCX 모델 스펙 (128K/32K, FC·SO·Thinking) | [검증됨] | NCP 공식 모델 문서 |
| Reranker / RAG Reasoning / Embedding v2 엔드포인트·스키마 | [검증됨] | NCP 공식 API 문서 (`api.ncloud-docs.com`) |
| OpenAI 호환 엔드포인트 및 지원 파라미터 | [검증됨] | NCP 공식 호환성 문서 |
| RPM/TPM 한도, 크레딧 규모, 임베딩 총비용 | **[미확인]** | 공식 문서 미기재 — 08.06 설명회 확인 필요 (U1·U2) |
| 임베딩/리랭커가 "LLM만" 제약에 포함되는지 | **[미확인]** | PDF 미명시 (U3) |
| AITHOR 프로바이더 `base_url` 주입 가능 + OpenRouter 상속 선례 | [검증됨] | `llm_providers.py:414/450/525/588/628` 직접 확인 |
| CLOVA 호환 계층에서 AITHOR Function Calling 무수정 동작 | [추정] | 코드·문서 대조 기반. **실호출 미검증** |
| AI-research-SKILLs에 한국어 형태소 분석 0건 | [검증됨] | SKILL.md 전수 grep |
| `pageindex` = Vectorless RAG, 계층 트리 + LLM 탐색, FinanceBench 98.7% 주장 | [검증됨/인용] | SKILL.md 전문 판독. **98.7%는 해당 문서의 주장이며 원논문·벤치마크 재현은 미검증** |
| PageIndex 인덱싱 비용 단점을 본 코퍼스가 회피 (트리 기성 보유) | [검증됨/논증] | `<TITLE>` 목차 실측(§2-5) + SKILL.md의 "인덱싱 비용" 한계 명시 대조 |
| FinanceBench 98.7%가 한국어 DART + HCX-007로 전이 | **[추정]** | 영문 문서·타 모델(Mafin 2.5) 기준 — 전이 근거 없음 |
| `76-finance-agent-skills`가 본 과제에 무관 + 제약 충돌 | [검증됨] | SKILL.md 판독 — DCF/센티먼트/트레이딩 대상, 데이터원 yfinance/opencli(외부 데이터 = 금지 조항) |
| `adaptive-chunking` 5 intrinsic metric 보유 | [검증됨] | SKILL.md frontmatter 판독 (LREC 2026, arXiv:2603.25333) |
