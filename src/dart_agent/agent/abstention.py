"""Abstention Gate — 답하지 않을 줄 아는 능력 (SPEC §6, 평가지표 7).

평가지표 7은 "답을 잘 하는 능력"이 아니라 "보유 데이터로 답할 수 없는 질의를 식별하고
무리한 답변 대신 한계 고지 또는 역질문으로 대응하는가"를 본다.

🔴 AC-AB2: 거부하고 끝내지 않는다. 확인 가능한 사실을 함께 제시하고 역질문한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..config import CORPUS_END, CORPUS_START

# 미래 예측·투자 판단 요구 탐지
PREDICTION_PATTERNS = (
    r"(오를|내릴|상승할|하락할|올라갈|떨어질)\s*(까|까요|것\s*같|전망|가능성)",
    r"(전망|예상|예측)\s*(해|해줘|해주|하면|은|는)\??$",
    r"(투자|매수|매도)\s*(해도|할까|하는\s*게|추천|의견|판단)",
    r"목표\s*주가",
    r"(유망|저평가|고평가)\s*(한|합니|인|일)",
    r"어떤\s*종목",
    # 🔴 매매 권유 — 어미 변형을 넓게 잡는다.
    #    "사도 될까"만 있어서 **"지금 사야 할까요?"가 통과**했다
    #    (Gold Set ABS-003으로 발견 — 2026-08-18).
    #    답하면 정확성·정보한계대응 두 항목에서 동시에 감점된다.
    #   사야 할까 · 사도 될까 · 팔아야 하나 · 매입해도 되나 …
    r"(사|팔아|매입|처분|매수|매도)\s*(해)?\s*(야|도|아야)?\s*(하|되|할|될)[^\s]*\s*(까|나|는지|지)",
    #   비중 확대/축소 · 담아도 될까 · 들어가도 되나
    r"(비중\s*(확대|축소)|담아|들어가)\w*\s*(도|야)?\s*(하|되|할|될)[^\s]*\s*(까|나)",
    #   지금/현재 + 매매 동사
    r"(지금|현재|이번에)\s*\S{0,3}\s*(사|팔|매수|매도)",
)

# 코퍼스 미보유 문서/정보 유형
UNSUPPORTED_PATTERNS = (
    (r"뉴스|기사|보도", "뉴스·보도 자료"),
    (r"애널리스트|증권사\s*리포트|리서치\s*리포트", "증권사 리포트"),
    (r"위키|백과", "위키·백과"),
    (r"실시간|현재\s*주가|지금\s*주가|오늘\s*주가", "실시간 시세"),
    (r"컨퍼런스\s*콜|IR\s*자료|기업\s*설명회", "IR 자료"),
    (r"특허|상표\s*등록", "특허·상표 정보"),
)

ABSTAIN_MESSAGES = {
    "out_of_universe": (
        "제공된 공시 데이터에서 해당 기업을 확인할 수 없습니다. "
        "본 시스템은 지정된 70개 상장기업의 공시(2023.01~2026.03)만 보유합니다."
    ),
    "out_of_period": (
        "요청하신 기간은 보유 공시 범위(2023년 1월 ~ 2026년 3월)를 벗어납니다."
    ),
    "no_evidence": (
        "제공된 공시 데이터에서 해당 내용을 확인할 수 없습니다."
    ),
    "unsupported_doctype": (
        "해당 정보는 보유 공시 유형(정기공시·주요사항보고서·거래소공시·지분공시)에 포함되지 않습니다."
    ),
    "forbidden_prediction": (
        "공시에 근거가 없는 미래 예측이나 투자 의견은 제공하지 않습니다."
    ),
    "ambiguous": (
        "질의를 확정하기 위해 추가 정보가 필요합니다."
    ),
    "low_unit_confidence": (
        "해당 수치의 금액 단위를 공시에서 확정할 수 없어 비교 결과를 제시하지 않습니다. "
        "잘못된 단위로 비교하면 최대 1,000배의 오차가 발생할 수 있습니다."
    ),
    "no_metric": (
        "질의에서 요청한 지표를 보유 재무 항목으로 특정할 수 없습니다."
    ),
}


@dataclass
class Abstention:
    reason: str
    message: str
    followup_questions: list[str] = field(default_factory=list)
    available_facts: list[str] = field(default_factory=list)

    def render(self) -> str:
        """AC-AB1: 한국어 완결 문장. AC-AB2: 확인 가능한 사실 + 역질문 동반."""
        parts = [self.message]
        if self.available_facts:
            parts.append(
                "다만 보유 공시로 확인 가능한 사실은 다음과 같습니다:\n"
                + "\n".join(f"- {f}" for f in self.available_facts)
            )
        if self.followup_questions:
            parts.append(
                "다음을 알려주시면 정확히 답변할 수 있습니다:\n"
                + "\n".join(f"- {q}" for q in self.followup_questions)
            )
        return "\n\n".join(parts)


def detect_prediction(question: str) -> bool:
    return any(re.search(p, question) for p in PREDICTION_PATTERNS)


def detect_unsupported(question: str) -> str | None:
    for pat, label in UNSUPPORTED_PATTERNS:
        if re.search(pat, question):
            return label
    return None


def period_out_of_range(years: list[int]) -> bool:
    if not years:
        return False
    lo, hi = int(CORPUS_START[:4]), int(CORPUS_END[:4])
    return all(y < lo or y > hi for y in years)


def decide(
    *,
    question: str,
    corp_codes: list[str],
    years: list[int],
    metric_key: str | None,
    has_facts: bool,
    top_search_score: float,
    threshold: float,
    unit_low_confidence: bool = False,
    is_comparison: bool = False,
    mentions_company: bool = False,
    available_facts: list[str] | None = None,
    unknown_company: str | None = None,
    similar_companies: list[str] | None = None,
) -> Abstention | None:
    """abstention 판정. None이면 정상 답변 경로로 진행 (SPEC §6).

    판정 순서가 계약이다 — 가장 확정적인 사유를 먼저 본다.
    """
    facts = available_facts or []
    similar_companies = similar_companies or []

    # 1) 금지 요구 — 가장 확정적
    if detect_prediction(question):
        return Abstention(
            "forbidden_prediction",
            ABSTAIN_MESSAGES["forbidden_prediction"],
            followup_questions=[
                "확인하고 싶은 재무 항목(매출액·영업이익 등)이나 사업 내용을 구체적으로 알려주세요."
            ],
            available_facts=facts,
        )

    # 2) 미보유 정보 유형
    label = detect_unsupported(question)
    if label:
        return Abstention(
            "unsupported_doctype",
            f"{ABSTAIN_MESSAGES['unsupported_doctype']} (요청 유형: {label})",
            available_facts=facts,
        )

    # 3) 유니버스 밖 — 기업을 언급했는데 해석 실패
    if mentions_company and not corp_codes:
        msg = ABSTAIN_MESSAGES["out_of_universe"]
        if unknown_company:
            # 무엇을 못 찾았는지 되짚어준다 — "확인할 수 없습니다"만으로는
            # 사용자가 오탈자인지 미보유인지 구분하지 못한다.
            msg = (
                f"'{unknown_company}'은(는) 제공된 공시 데이터에 없습니다. "
                "본 시스템은 지정된 70개 상장기업의 공시(2023.01~2026.03)만 보유합니다."
            )
        followups = ["보유 70개 기업 중 어느 기업의 공시를 확인할까요?"]
        if similar_companies:
            # 🔴 거부하고 끝내지 않는다 (AC-AB2). 계열 접두를 공유하는 보유 기업을
            #    제시하면 사용자가 바로 다음 질의를 만들 수 있다.
            followups = [
                "보유 기업 중 관련될 수 있는 곳은 "
                + ", ".join(similar_companies)
                + "입니다. 이 중 어느 기업의 공시를 확인할까요?"
            ]
        return Abstention(
            "out_of_universe", msg,
            followup_questions=followups,
            # 🔴 종전에는 이 경로만 available_facts가 비어 있었다 — AC-AB2는
            #    "확인 가능한 사실을 함께 제시"를 요구하는데 유독 여기서 누락됐다.
            available_facts=facts,
        )

    # 4) 기간 밖
    if period_out_of_range(years):
        return Abstention(
            "out_of_period",
            f"{ABSTAIN_MESSAGES['out_of_period']} (요청: {sorted(set(years))})",
            available_facts=facts,
        )

    # 5) 비교 질의인데 단위 불확정 — 1,000배 오차 방지 (R5b)
    if is_comparison and unit_low_confidence:
        return Abstention(
            "low_unit_confidence",
            ABSTAIN_MESSAGES["low_unit_confidence"],
            available_facts=facts,
        )

    # 6) 지표 특정 실패
    if metric_key is None and not has_facts and top_search_score < threshold:
        return Abstention(
            "no_metric",
            ABSTAIN_MESSAGES["no_metric"],
            followup_questions=[
                "매출액·영업이익·당기순이익·자산총계·설비투자 등 구체적 항목명으로 질문해 주세요."
            ],
            available_facts=facts,
        )

    # 7) 근거 부족
    if not has_facts and top_search_score < threshold:
        return Abstention(
            "no_evidence", ABSTAIN_MESSAGES["no_evidence"], available_facts=facts
        )

    # 8) 기업·기간 미특정 (모호) — 근거는 있으나 대상이 불분명
    if not corp_codes and not mentions_company:
        return Abstention(
            "ambiguous",
            ABSTAIN_MESSAGES["ambiguous"],
            followup_questions=[
                "어느 기업의 공시를 확인할까요?",
                "기준 연도와 연결/별도 기준을 알려주세요.",
            ],
            available_facts=facts,
        )

    return None
