# TASKS — 공시 Agent 구현 태스크 (DAG + 팀 배정)

> 근거: [`SPEC.md`](./SPEC.md) · 의존성 표기는 CLAUDE.md 「의존성 표기 권장」 규약 준수

---

## 실행 Wave (DAG)

```
Wave 0 (직렬, 메인)     T001 ─────────────────────────────┐  공유 계약 확립
                                                          │
Wave 1 (병렬, 팀 4인)   T010 T011 T012 T013 ◄─────────────┤  파서 4종 (독립 파일)
                                                          │
Wave 2 (직렬, 메인)     T020 T021 ◄──────────────────────┤  스토어 + 빌더
                                                          │
Wave 3 (병렬, 팀 2인)   T030 T031 ◄──────────────────────┤  검색 · 도구
                                                          │
Wave 4 (직렬, 메인)     T040 T041 T042 ◄─────────────────┘  검증기 · API · 프로바이더
                                                        
Wave 5 (직렬, 메인)     T050 T051                            테스트 · HARD GATE
```

**병렬 안전성 근거**: Wave 1의 4개 태스크는 **서로 다른 파일만** 생성한다 (파일 충돌 0).
Wave 3도 동일. 따라서 worktree 격리 불필요 — 오버헤드 회피.

---

## Wave 0 — 공유 계약 (메인 직접, 위임 불가)

### T001 · 공유 모델 + 유틸 + 지표 사전
- 파일: `src/dart_agent/models.py` · `numbers.py` · `metrics.py` · `config.py`
- 내용: SPEC §1-1 dataclass 전체 · SPEC §1-4 단위 정규화 · SPEC §1-3 지표 사전 · 설정
- AC: `AC-U1~U3`, `AC-M1~M2`
- **위임 불가 이유**: Wave 1 팀 4인이 전부 이 계약에 의존. 계약이 흔들리면 4개 산출물이 동시에 깨진다.

---

## Wave 1 — 파서 4종 (팀 병렬 위임)

### T010 · PeriodicParser (depends on T001) — 난이도 최상
- 파일: `src/dart_agent/parsers/periodic.py` (단독)
- 내용: Stage A 목차 트리(`<TITLE>` → path) · Stage B XBRL(`<TE ACODE ACONTEXT>`) · Stage C `III-1 요약재무정보` 표 폴백 · Stage D 단위 정규화 · 섹션 `content_class` 분류
- AC: `AC-P1~P3`, `AC-U1~U3`
- 담당: **Builder-A**

### T011 · ExchangeParser (depends on T001) — 함정 주의
- 파일: `src/dart_agent/parsers/exchange.py` (단독)
- 내용: 🔴 **UTF-8 강제 디코딩** (meta euc-kr 무시) · key-value 표 → `ContractEvent` · 정정 diff 표 → `CorrectionDiff` · 체결/해지 구분
- AC: `AC-P1~P3`, `AC-TEST2`
- 담당: **Builder-B**

### T012 · HoldingParser (depends on T001)
- 파일: `src/dart_agent/parsers/holding.py` (단독)
- 내용: `<TE ACODE>` 전 필드 → `HoldingEvent` (`RPT_RSP_NM`/`SUM_BMT_CNT`/`SUM_BMT_RT`/`SUM_TMT_CNT`/`SUM_TMT_RT`/`SUM_CHN_RWN`/`BFR_RPT_DT`/`THS_RPT_DT`)
- AC: `AC-P1~P3`, `AC-C2`
- 담당: **Builder-C**

### T013 · MajorParser (depends on T001)
- 파일: `src/dart_agent/parsers/major.py` (단독)
- 내용: `DOCUMENT-NAME ACODE` → `event_kind` 25종 분류 (자기주식처분/취득·유상증자·CB·BW·EB·합병·분할·감자·조건부자본증권…) · 금액 추출 → `CapitalEvent` · 정정 diff
- AC: `AC-P1~P3`
- 담당: **Builder-D**

---

## Wave 2 — 스토어 (메인 직접)

### T020 · 스키마 + 리포지토리 (depends on T010,T011,T012,T013)
- 파일: `src/dart_agent/store/schema.sql` · `store/db.py` · `store/repository.py`
- 내용: SPEC §2-1 스키마 · idempotent upsert · 트랜잭션
- AC: `AC-S1~S3`

### T021 · 인덱스 빌더 + 별칭 + 정정 체인 (depends on T020)
- 파일: `scripts/build_index.py` · `store/alias.py` · `store/corrections.py`
- 내용: manifest 순회 → 파서 디스패치 → 적재 · 별칭 4-way + 수기 8종 · 정정 체인 해소 + **매칭률 리포트**
- AC: `AC-S4`, `AC-A1~A4`, `AC-C1~C4`

---

## Wave 3 — 검색 · 도구 (팀 병렬 위임)

### T030 · 한국어 BM25 + 섹션 주소 (depends on T021)
- 파일: `src/dart_agent/retrieval/tokenizer.py` · `bm25.py` · `section_map.py` · `fusion.py`
- 내용: kiwipiepy + n-gram 폴백 · BM25(k1=1.2,b=0.75) · RRF(k=60) · 주소 사전 10종 · `EmbeddingProvider=None` 시 BM25 단독
- AC: `AC-R1~R6`
- 담당: **Builder-E**

### T031 · Agent 도구 6종 (depends on T021)
- 파일: `src/dart_agent/agent/tools.py`
- 내용: `fact_query`/`get_section`/`doc_search`/`event_query`/`trace_chain`/`compute` — 전부 순수 함수, 출처 동반 반환
- AC: `AC-T1~T4`
- 담당: **Builder-F**

---

## Wave 4 — 검증 · API (메인 직접)

### T040 · 검증기 V1~V5 + Abstention (depends on T031)
- 파일: `src/dart_agent/agent/verifier.py` · `abstention.py`
- 내용: SPEC §5 V1~V5 · §6 abstention 7종 · 숫자 추출(한국어 표기 포함)
- AC: `AC-V1~V3`, `AC-AB1~AB3`
- **위임 불가 이유**: 환각 차단의 핵심. 확증편향 회피를 위해 독립 리뷰는 붙이되 구현은 메인.

### T041 · LLM 프로바이더 + 스텁 (depends on T001)
- 파일: `src/dart_agent/llm/provider.py` · `clova.py` · `stub.py`
- 내용: OpenAI 호환 `ClovaProvider` · 키 없을 때 `StubProvider` 자동 폴백 + 경고
- AC: `AC-L1~L4`

### T042 · Orchestrator + FastAPI (depends on T030,T031,T040,T041)
- 파일: `src/dart_agent/agent/orchestrator.py` · `api/server.py`
- 내용: Q-Understanding(규칙 기반 fast-path + LLM 보강) · 도구 라우팅 T1~T6 · `think_trace` 5단 구조 · `GET /answer` 계약 · 500 금지 · 캐시
- AC: `AC-0`, `AC-API1~API6`

---

## Wave 5 — 검증 (메인 직접)

### T050 · 테스트 스위트 (depends on T042)
- 파일: `tests/unit/test_*.py` · `tests/fixtures/`
- AC: `AC-TEST1~TEST7`

### T051 · HARD GATE + 실코퍼스 E2E (depends on T050)
- 내용: 실제 코퍼스 부분 인덱싱 → `GET /answer` T1 질의 정답 확인 → `pytest` 전체 통과
- AC: `D1~D10` 전체

---

## 팀 구성 (6 Builder + 메인 Orchestrator)

| 역할 | 담당 태스크 | model | effort tier |
|---|---|---|---|
| Orchestrator (메인) | T001·T020·T021·T040·T041·T042·T050·T051 | opus | deep/xhigh |
| Builder-A | T010 PeriodicParser | opus | deep |
| Builder-B | T011 ExchangeParser | opus | standard |
| Builder-C | T012 HoldingParser | opus | standard |
| Builder-D | T013 MajorParser | opus | standard |
| Builder-E | T030 검색 | opus | standard |
| Builder-F | T031 도구 | opus | standard |

> 모델은 전 역할 `opus` (2026-07-25b 정책). 차등은 effort tier.
> Wave 1·3만 위임 — Wave 0/2/4/5는 공유 계약·환각 차단·통합이라 메인 유지 (`selective-subagent.md` Rule 2).

---

## 진행 체크리스트

- [ ] T001 공유 모델·유틸·지표 사전
- [ ] T010 PeriodicParser
- [ ] T011 ExchangeParser
- [ ] T012 HoldingParser
- [ ] T013 MajorParser
- [ ] T020 스키마·리포지토리
- [ ] T021 빌더·별칭·정정 체인
- [ ] T030 BM25·섹션 주소
- [ ] T031 도구 6종
- [ ] T040 검증기·Abstention
- [ ] T041 프로바이더·스텁
- [ ] T042 Orchestrator·API
- [ ] T050 테스트
- [ ] T051 HARD GATE + E2E
