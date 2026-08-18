"""지표 정규화 사전 (SPEC §1-3).

질의 표현 → metric_key → (XBRL ACODE 집합, 표 라벨 정규식).
정규식 first-match이며 순서가 명세된다 (AC-M1). 미등록 표현은 None → abstention (AC-M2).

ACODE는 실측 확인된 것만 등재한다 (proposal §2-4):
  ifrs-full_Revenue / dart_OperatingIncomeLoss / ifrs-full_ProfitLoss
  ifrs-full_Assets / ifrs-full_Equity / ifrs-full_CurrentAssets
  ifrs-full_CashAndCashEquivalents
미확인 ACODE는 후보로 두되 표 라벨 폴백이 실질 경로다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .models import DURATION, INSTANT


@dataclass(frozen=True)
class MetricDef:
    key: str
    label: str  # 사람이 읽는 대표 명칭
    acodes: tuple[str, ...]
    label_re: str  # 표 행 라벨 매칭
    query_re: str  # 사용자 질의 표현 매칭
    period_kind: str  # instant(재무상태표) | duration(손익계산서)


# 🔴 순서가 계약이다 — first-match wins. 좁은 표현을 먼저 둔다.
METRICS: tuple[MetricDef, ...] = (
    MetricDef(
        key="operating_income",
        label="영업이익",
        acodes=("dart_OperatingIncomeLoss", "dart_OperatingIncome"),
        label_re=r"^\s*영업이익",
        query_re=r"영업\s*이익|영업손익",
        period_kind=DURATION,
    ),
    MetricDef(
        key="net_income",
        label="당기순이익",
        acodes=("ifrs-full_ProfitLoss",),
        label_re=r"당기\s*순이익|^\s*순이익",
        query_re=r"당기\s*순이익|순\s*이익|순손익",
        period_kind=DURATION,
    ),
    MetricDef(
        key="revenue",
        label="매출액",
        acodes=("ifrs-full_Revenue", "dart_Revenue"),
        label_re=r"^\s*매출액\s*$|^\s*영업수익|^\s*매출\s*$|^\s*수익\(매출액\)",
        query_re=r"매출\s*액|매출|영업\s*수익",
        period_kind=DURATION,
    ),
    MetricDef(
        key="ppe_acquisition",
        label="유형자산 취득(설비투자)",
        acodes=(
            "dart_PurchaseOfPropertyPlantAndEquipment",
            "ifrs-full_PurchaseOfPropertyPlantAndEquipment",
        ),
        label_re=r"유형자산.*(취득|증가)|설비\s*투자|시설\s*투자",
        query_re=r"설비\s*투자|시설\s*투자|capex|CAPEX|유형자산\s*취득",
        period_kind=DURATION,
    ),
    MetricDef(
        key="rnd_expense",
        label="연구개발비",
        acodes=("dart_ResearchAndDevelopmentExpense",),
        label_re=r"연구\s*개발\s*비",
        query_re=r"연구\s*개발\s*비|R&D|알앤디",
        period_kind=DURATION,
    ),
    MetricDef(
        key="total_assets",
        label="자산총계",
        acodes=("ifrs-full_Assets",),
        label_re=r"^\s*자산\s*총계",
        query_re=r"자산\s*총계|총\s*자산",
        period_kind=INSTANT,
    ),
    MetricDef(
        key="total_liabilities",
        label="부채총계",
        acodes=("ifrs-full_Liabilities",),
        label_re=r"^\s*부채\s*총계",
        query_re=r"부채\s*총계|총\s*부채",
        period_kind=INSTANT,
    ),
    MetricDef(
        key="total_equity",
        label="자본총계",
        acodes=("ifrs-full_Equity",),
        label_re=r"^\s*자본\s*총계",
        query_re=r"자본\s*총계|총\s*자본|자기\s*자본",
        period_kind=INSTANT,
    ),
    MetricDef(
        key="current_assets",
        label="유동자산",
        acodes=("ifrs-full_CurrentAssets",),
        label_re=r"유동\s*자산",
        query_re=r"유동\s*자산",
        period_kind=INSTANT,
    ),
    MetricDef(
        key="cash",
        label="현금및현금성자산",
        acodes=("ifrs-full_CashAndCashEquivalents",),
        label_re=r"현금\s*및\s*현금성\s*자산|^\s*현금성\s*자산",
        query_re=r"현금\s*및?\s*현금성\s*자산|보유\s*현금",
        period_kind=INSTANT,
    ),
)

_BY_KEY = {m.key: m for m in METRICS}
_BY_ACODE: dict[str, MetricDef] = {}
for _m in METRICS:
    for _a in _m.acodes:
        _BY_ACODE.setdefault(_a, _m)

_QUERY_PATTERNS = tuple((re.compile(m.query_re, re.I), m) for m in METRICS)
_LABEL_PATTERNS = tuple((re.compile(m.label_re), m) for m in METRICS)


def by_key(key: str | None) -> MetricDef | None:
    return _BY_KEY.get(key) if key else None


def from_query(text: str) -> MetricDef | None:
    """사용자 질의 텍스트에서 지표를 식별한다. 미등록 표현은 None (AC-M2)."""
    if not text:
        return None
    for pat, m in _QUERY_PATTERNS:
        if pat.search(text):
            return m
    return None


def from_acode(acode: str | None) -> MetricDef | None:
    """XBRL ACODE → 지표. 미등재 ACODE는 None (fact는 여전히 저장되나 조회키 없음)."""
    return _BY_ACODE.get(acode) if acode else None


def from_label(label: str | None) -> MetricDef | None:
    """표 행 라벨 → 지표 (Stage C 폴백 경로)."""
    if not label:
        return None
    for pat, m in _LABEL_PATTERNS:
        if pat.search(label):
            return m
    return None


def all_keys() -> tuple[str, ...]:
    return tuple(_BY_KEY)
