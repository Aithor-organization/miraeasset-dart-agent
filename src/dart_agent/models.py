"""파서 ↔ 스토어 경계의 정규화 레코드 (SPEC §1-1).

모든 파서는 이 dataclass만 반환한다. DB/네트워크 접근 금지 (AC-P2).
frozen=True — 파서 출력은 불변 (결정론 보장, AC-P3).
"""

from __future__ import annotations

from dataclasses import dataclass, field

# doc_group 리터럴
PERIODIC = "periodic"
MAJOR = "major"
EXCHANGE = "exchange"
HOLDING = "holding"
DOC_GROUPS = (PERIODIC, MAJOR, EXCHANGE, HOLDING)

# basis 리터럴
CONSOLIDATED = "consolidated"
SEPARATE = "separate"

# period_kind 리터럴 — ACONTEXT의 e(기말시점) / d(기중기간)
INSTANT = "instant"
DURATION = "duration"

# period_scope 리터럴 — ACONTEXT의 기간 범위 접미.
# 🔴 실측: 반기·분기 보고서는 `dHYA`(누적 6개월) / `dHYQ`(당기 3개월)를 함께 담는다.
#    이를 구분하지 않으면 누적값과 당기값이 같은 지표로 섞여 오답이 된다.
SCOPE_FY = "FY"    # 연간
SCOPE_HYA = "HYA"  # 반기 누적
SCOPE_HYQ = "HYQ"  # 반기 보고서 내 당기 3개월
SCOPE_QTA = "QTA"  # 분기 누적
SCOPE_QTQ = "QTQ"  # 분기 당기 3개월

# content_class 리터럴 — 임베딩 선별 정책의 축 (SPEC §3-1 AC-R3)
PROSE = "prose"
TABLE_REGISTRY = "table_registry"
FINANCIAL_STMT = "financial_stmt"


@dataclass(frozen=True)
class DocMeta:
    """manifest.jsonl 1행 = 문서 1건."""

    doc_id: str
    corp_code: str  # 8자리 문자열 — 선행 0 보존
    corp_name: str  # DART 공식 법인명 = raw/ 하위 폴더명 (조인 키)
    doc_group: str
    doc_subtype: str | None
    report_nm: str
    rcept_no: str
    rcept_dt: str  # YYYYMMDD
    is_correction: bool
    base_year: int | None
    base_month: int | None
    file_path: str  # corpus root 기준 상대경로
    file_format: str  # xml | pdf+html
    listed_name: str | None = None
    stock_code: str | None = None
    industry: str | None = None
    sector: str | None = None


@dataclass(frozen=True)
class FinFact:
    """재무 사실 1건. value_krw는 항상 '원' 단위 정규화 (AC-U1)."""

    doc_id: str
    corp_code: str
    label_ko: str
    fy: int
    period_kind: str
    basis: str
    raw_value: str
    unit_confidence: str  # high | low
    source: str  # xbrl | table
    acode: str | None = None
    metric_key: str | None = None
    axis: str | None = None
    value_krw: int | None = None
    raw_unit: str | None = None
    src_section: str | None = None
    # 기간 범위 (FY/HYA/HYQ/QTA/QTQ). 누적↔당기 혼합 방지용 — 조회 시 필수 필터.
    period_scope: str | None = None


@dataclass(frozen=True)
class Section:
    """정기공시 섹션 1건. path가 법정 목차 주소 (SPEC §3-3)."""

    section_id: str  # {doc_id}#III-2-2
    doc_id: str
    corp_code: str
    path: str
    title: str
    level: int
    text: str
    tables_md: str
    char_len: int
    content_class: str


@dataclass(frozen=True)
class ContractEvent:
    """거래소공시 계약 이벤트."""

    doc_id: str
    corp_code: str
    event_kind: str  # 체결 | 해지 | 신규시설투자 | 투자판단관련
    contract_kind: str | None = None
    detail: str | None = None
    counterparty: str | None = None
    amount_krw: int | None = None
    recent_revenue_krw: int | None = None
    ratio_pct: float | None = None
    start_dt: str | None = None
    end_dt: str | None = None
    decision_dt: str | None = None


@dataclass(frozen=True)
class CapitalEvent:
    """주요사항보고서 자금조달·자기주식 등 이벤트."""

    doc_id: str
    corp_code: str
    event_kind: str
    amount_krw: int | None = None
    decision_dt: str | None = None
    detail_json: str = "{}"


@dataclass(frozen=True)
class HoldingEvent:
    """지분공시(5% 보고). prev_report_dt가 체인 포인터 (AC-C2)."""

    doc_id: str
    corp_code: str
    reporter: str | None = None
    cnt_before: int | None = None
    rate_before: float | None = None
    cnt_after: int | None = None
    rate_after: float | None = None
    change_reason: str | None = None
    report_dt: str | None = None
    prev_report_dt: str | None = None


@dataclass(frozen=True)
class CorrectionDiff:
    """정정공시의 원본 포인터 + 항목별 정정전/정정후."""

    doc_id: str
    target_doc_kind: str | None = None
    target_submit_dt: str | None = None  # YYYYMMDD
    reason: str | None = None
    item: str | None = None
    before_val: str | None = None
    after_val: str | None = None


@dataclass(frozen=True)
class RegistryRow:
    """임원·계열사·종속회사 등 레지스트리 표 1행 (임베딩 제외 대상)."""

    doc_id: str
    registry_kind: str
    row_json: str
    src_section: str | None = None


@dataclass
class ParseResult:
    """파서 산출물. 예외 대신 warnings로 부분 실패를 보고한다 (AC-P1)."""

    meta: DocMeta
    fin_facts: list[FinFact] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)
    contract_events: list[ContractEvent] = field(default_factory=list)
    capital_events: list[CapitalEvent] = field(default_factory=list)
    holding_events: list[HoldingEvent] = field(default_factory=list)
    corrections: list[CorrectionDiff] = field(default_factory=list)
    registry_rows: list[RegistryRow] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    @property
    def record_count(self) -> int:
        return (
            len(self.fin_facts)
            + len(self.sections)
            + len(self.contract_events)
            + len(self.capital_events)
            + len(self.holding_events)
            + len(self.corrections)
            + len(self.registry_rows)
        )
