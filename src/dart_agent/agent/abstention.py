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

# 재무 건전성·투자 적합성의 **판정**을 요구하는 질의 (2026-09-03, OOD 실측).
#
#   "LG에너지솔루션 부채비율이 위험한 수준이야?" → 종전에는 기권하지 않고
#   요약재무정보 표를 그대로 뱉었다. 공시에는 "위험한 수준"이라는 판정이 없다 —
#   그건 우리가 만들어낸 투자 의견이므로 근거 기반(지표 4)에서 감점된다.
#   반대로 기권하면 정보한계 대응(지표 7)에서 가점 대상이 된다.
#
# 🔴 사실 조회와 가르는 축은 **서술어 자리의 형용사**다.
#      기권 O: "부채비율이 **위험한 수준**이야?"   (판정을 물음)
#      기권 X: "주요 **위험요인**은 무엇인가?"      (공시 기재 항목을 물음)
#      기견 X: "**위험관리** 정책을 설명해줘"       (섹션명)
#   그래서 형용사 뒤에 판정 명사(수준·편·상태) 또는 판정 어미(가요·을까·은가)를
#   요구한다. 명사 단독("위험요인"·"위험관리")은 어느 쪽에도 닿지 않는다.
JUDGMENT_PATTERNS = (
    #   위험한 수준 · 안전한 편 · 건전한 상태 · 과도한 수준
    r"(위험|안전|건전|양호|우량|불안|부실|과도|적정|취약)\w{0,2}\s*(수준|편|상태|상황)",
    #   안전한가요? · 괜찮을까요? · 건전한가 · 위험하지 않나
    r"(위험|안전|건전|양호|우량|괜찮|불안|부실|취약)\w{0,3}"
    r"(까요|나요|가요|은가|는가|한가|을까|ㄹ까|지\s*않)",
    #   재무구조가 좋아? · 실적이 나쁜가 · 수익성이 우수한가
    r"(재무|부채|실적|수익성|성장성|안정성|유동성|재무구조)\S{0,3}\s*(이|가)\s*"
    r"\S{0,4}(좋|나쁘|괜찮|우수|열악|튼튼|허약)",
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
    "forbidden_judgment": (
        "공시 데이터는 재무 수치와 기재 사항을 제공하지만, 그 수치가 '위험한지'·'양호한지'에 "
        "대한 판정은 공시에 존재하지 않습니다. 투자 판단에 해당하므로 제공하지 않습니다."
    ),
    "forbidden_prediction": (
        "공시에 근거가 없는 미래 예측이나 투자 의견은 제공하지 않습니다."
    ),
    "no_comparison_metric": (
        "비교를 요청하셨으나 어떤 지표로 비교할지 질의에서 특정되지 않았습니다. "
        "공시 데이터로 비교하려면 기준 지표가 필요합니다."
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


def detect_judgment(question: str) -> bool:
    return any(re.search(p, question) for p in JUDGMENT_PATTERNS)


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

    # 1-bis) 건전성·적합성 **판정** 요구 — 예측과 같은 층위의 금지 요구다.
    #        수치는 있지만 "위험한지"는 공시에 없다. 확인 가능한 사실은 함께 준다.
    if detect_judgment(question):
        return Abstention(
            "forbidden_judgment",
            ABSTAIN_MESSAGES["forbidden_judgment"],
            followup_questions=[
                "부채비율·유동비율 등 구체적 재무 수치를 물어보시면 공시 기준으로 답변드립니다.",
                "해당 기업이 공시에 기재한 위험요인(사업보고서 '위험관리' 항목)을 확인해 드릴까요?",
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

    # 3) 유니버스 밖 — 기업을 **구체적으로 지목**했는데 해석 실패
    #
    # 🔴 조건이 `mentions_company`가 아니라 `unknown_company`인 이유 (2026-09-02 정정):
    #    `mentions_company`는 "기업|회사|사|㈜" 정규식이라 **일반명사에도 켜진다**.
    #    그래서 대상이 실제로 불분명한 질의까지 out_of_universe로 갔다 —
    #      "2024년 매출액 상위 **기업**은?" → "해당 기업을 확인할 수 없습니다"
    #      "**회사**의 2024년 매출액은?"    → 동일
    #    지목한 적 없는 기업을 못 찾았다고 답하는 셈이라 되묻는 편(ambiguous)이 옳다.
    #    반대로 `존재하지않는기업㈜의 영업이익은?`처럼 진짜 지목한 경우는
    #    소유격 경로가 주체를 뽑아내므로 여기서 그대로 잡힌다.
    if unknown_company and not corp_codes:
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

    # 5-bis) 비교 질의인데 **비교 축이 없다** (2026-09-03, OOD 실측).
    #
    #   "현대차와 기아 중 어디가 더 성장했어?" → 두 기업은 해석됐고 T3_compare로
    #   분류됐지만 '성장'에 대응하는 지표가 없다. 종전에는 섹션 원문/검색 결과로
    #   폴백해 **한쪽 기업만** 답하거나 "근거를 참고하세요"만 냈다 — 요구한 비교
    #   판정은 어느 쪽으로도 나오지 않는다.
    #
    #   '성장'을 매출액으로 임의 해석하지 않는다. 그건 우리가 정한 기준이지
    #   질의가 정한 기준이 아니라서, 맞아도 근거 기반(지표 4)에서 방어할 수 없다.
    #   역질문이 정답이다 (지표 7이 명시적으로 요구하는 대응).
    #
    #   🔴 골드셋 비교계열 75건은 **전부 지표가 특정된다**(2026-09-03 실측) —
    #      이 규칙은 그 75건에 닿지 않는다.
    if is_comparison and metric_key is None and len(corp_codes) >= 2:
        return Abstention(
            "no_comparison_metric",
            ABSTAIN_MESSAGES["no_comparison_metric"],
            followup_questions=[
                "매출액·영업이익·당기순이익·자산총계·설비투자 중 어떤 지표로 비교할까요?",
                "비교 기준 연도와 연결/별도 기준도 함께 알려주세요.",
            ],
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
