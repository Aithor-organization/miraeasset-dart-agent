"""법정 목차 주소 사전 (SPEC §3-3 AC-R6, 설계 D2 "Address before Search").

근거: 정기공시는 법정 서식이라 목차 골격이 기업 무관 동일하다
      (12개사·10섹터 표본 12/12 핵심 5섹션 보유 — proposal §2-5 실측).
      따라서 "2026년 1분기 보고서 기준 주요 투자 계획"은 벡터 검색 없이
      (corp, 2026Q1, path IN ('II-3','II-4','II-6')) 조회로 처리된다.
"""

from __future__ import annotations

import re

# 질의 의도 → 섹션 주소. 순서가 계약이다 (first-match 누적).
INTENT_PATHS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("core_business", r"핵심\s*사업|주요\s*사업|사업\s*개요|사업\s*내용|어떤\s*사업", ("II-1", "II-2")),
    ("products", r"제품|서비스\s*종류|상품", ("II-2",)),
    ("capex", r"설비\s*투자|시설\s*투자|생산\s*설비|투자\s*계획|capex", ("II-3", "II-4")),
    ("sales_orders", r"매출\s*및?\s*수주|수주\s*상황|수주\s*잔고|판매\s*경로", ("II-4",)),
    ("risk", r"위험\s*관리|파생\s*거래|헤지", ("II-5",)),
    ("contracts_rnd", r"주요\s*계약|연구\s*개발|R&D", ("II-6",)),
    ("summary_fin", r"요약\s*재무|재무\s*요약|주요\s*재무\s*지표", ("III-1",)),
    ("consolidated_bs", r"연결\s*재무상태표|연결\s*대차대조", ("III-2-1",)),
    ("consolidated_is", r"연결\s*손익|연결\s*포괄손익", ("III-2-2", "III-2-3")),
    ("consolidated_cf", r"연결\s*현금흐름", ("III-2-5",)),
    ("separate_bs", r"별도\s*재무상태표|개별\s*재무상태표", ("III-4-1",)),
    ("separate_is", r"별도\s*손익|개별\s*손익", ("III-4-2",)),
    ("notes", r"주석|회계\s*정책|특수관계자", ("III-3",)),
    ("mdna", r"경영진단|경영\s*분석|MD&A|분석\s*의견", ("IV",)),
    ("audit", r"외부\s*감사|감사인|감사\s*의견", ("V",)),
    ("governance", r"이사회|지배구조|사외이사|감사위원", ("VI",)),
    ("shareholders", r"주주|최대주주|지분\s*구조|주식\s*소유", ("VII",)),
    ("officers", r"임원|직원\s*현황|보수|급여", ("VIII",)),
    ("affiliates", r"계열\s*회사|관계사|종속\s*회사|타법인\s*출자", ("IX",)),
)

_COMPILED = tuple((name, re.compile(pat), paths) for name, pat, paths in INTENT_PATHS)

# 기간 표현 → (base_year, doc_subtype)
_PERIOD = re.compile(
    r"(?P<y>20\d{2})\s*년?\s*(?:(?P<q>[1-4])\s*분기|(?P<h>상|하)\s*반기|(?P<fy>사업연도|연간|연결기준|전체)?)"
)


def paths_for(question: str) -> list[str]:
    """질의에서 섹션 주소를 추론한다. 매칭 없으면 빈 리스트 → doc_search 폴백."""
    out: list[str] = []
    for _name, pat, paths in _COMPILED:
        if pat.search(question):
            for p in paths:
                if p not in out:
                    out.append(p)
    return out


def intents_for(question: str) -> list[str]:
    return [name for name, pat, _ in _COMPILED if pat.search(question)]


def parse_period(question: str) -> tuple[int | None, str | None]:
    """질의 → (base_year, doc_subtype). doc_subtype ∈ annual|half|quarter|None."""
    m = _PERIOD.search(question or "")
    if not m:
        years = re.findall(r"(20\d{2})\s*년", question or "")
        return (int(years[-1]), None) if years else (None, None)
    year = int(m.group("y"))
    if m.group("q"):
        return year, "quarter"
    if m.group("h"):
        return year, "half"
    return year, "annual" if m.group("fy") else None


def parse_years(question: str) -> list[int]:
    """질의에 등장하는 모든 연도 (비교·시계열 질의용)."""
    return sorted({int(y) for y in re.findall(r"(20\d{2})\s*년?", question or "")})


# 🔴 누적(A) / 당기(Q) 구분 — 함정 7의 질의 측 대응.
#
# `parse_period`는 보고서 종류(annual/half/quarter)만 알려주고 기간 범위는 모른다.
# 그래서 "상반기 매출"을 물어도 fact_query가 연간 값을 반환했다
# (Gold Set scope_split 0/20으로 발견 — 2026-08-18).
#
# 실측: 삼성전자 2025 반기 매출 — 누적 153.7조 vs 당기 74.6조. 두 배 차이다.
_ACCUM = re.compile(r"누적")
_STANDALONE = re.compile(r"당기|3\s*개월|단독")
_HALF = re.compile(r"반기|상반기|2\s*분기|2Q", re.I)
_QUARTER = re.compile(r"([13-4])\s*분기|[13-4]Q", re.I)
_ANNUAL = re.compile(r"연간|사업연도|연결기준\s*연간|한\s*해")


def parse_scope(question: str) -> str | None:
    """질의 → period_scope 코드. 판단 불가면 None (호출자가 기존 우선순위 사용).

    반환: FY | HYA | HYQ | QTA | QTQ | None

    관행 기본값:
      "상반기"  → 누적 6개월 (HYA)  — 명시 없으면 누적으로 읽는 것이 관행
      "2분기"   → 당기 3개월 (HYQ)  — 분기를 지목하면 그 분기 실적을 뜻한다
    """
    text = question or ""
    accum = bool(_ACCUM.search(text))
    standalone = bool(_STANDALONE.search(text))

    if _QUARTER.search(text):
        # 1·3·4분기 — 누적 명시가 있으면 QTA, 아니면 당기
        return "QTA" if accum and not standalone else "QTQ"

    if _HALF.search(text):
        if standalone and not accum:
            return "HYQ"          # "2분기 3개월" · "당기"
        if accum:
            return "HYA"          # "상반기 누적"
        # 무수식 — "상반기"는 누적, "2분기"는 당기로 읽는다
        return "HYQ" if re.search(r"2\s*분기|2Q", text, re.I) else "HYA"

    if _ANNUAL.search(text):
        return "FY"

    return None


def parse_basis(question: str) -> str | None:
    """연결/별도 기준. 명시 없으면 None (호출자가 기본값 결정)."""
    if re.search(r"별도\s*기준|개별\s*기준|별도\s*재무|개별\s*재무", question):
        return "separate"
    if re.search(r"연결\s*기준|연결\s*재무|연결\s*기준으로", question):
        return "consolidated"
    return None
