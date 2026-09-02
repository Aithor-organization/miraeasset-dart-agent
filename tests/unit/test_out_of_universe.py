"""유니버스 밖 기업 질의 — 기권 사유가 ambiguous가 아니라 out_of_universe여야 한다.

실측 결함 (2026-09-02, NCP 리허설 서버):
  "LG화학의 주요 위험요인은 무엇인가?" → abstain_reason=ambiguous
  응답: "질의를 확정하기 위해 추가 정보가 필요합니다 / 어느 기업의 공시를 확인할까요?"

LG화학은 70개 유니버스에 없으므로 **기권 자체는 옳다**. 틀린 것은 사유와 문구다 —
기업명을 명시한 사용자에게 "어느 기업이냐"고 되묻는 응답은 정보한계 대응에서 감점된다.
원인은 `mentions_company`가 "기업|회사|사|㈜" 정규식에만 의존한 것이었다.
(Q-07 "존재하지않는**회사**"가 통과했던 건 우연히 `사`가 들어갔기 때문이다.)
"""

from __future__ import annotations

import pytest

from dart_agent.agent.abstention import decide
from dart_agent.store.alias import detect_company_mention

# ── 형태 감지: 잡아야 하는 것 ────────────────────────────────────────────────
#    ⚠️ 여기 있다고 전부 유니버스 밖이라는 뜻은 아니다 — 순수 함수 검사다.
#       실제 경로에서는 별칭 해석이 먼저 돌고, 보유 기업이면 이 함수는 호출되지 않는다
#       (예: 한화오션은 유니버스 안이라 corp_code로 해석된다 — 실측 확인).
DETECT = [
    ("LG화학의 주요 위험요인은 무엇인가?", "LG화학"),          # 실측 결함 케이스
    ("SK이노베이션의 2024년 매출액은?", "SK이노베이션"),
    ("한화오션의 수주 잔고를 알려줘", "한화오션"),
    ("두산에너빌리티의 사업 부문은?", "두산에너빌리티"),
    ("삼성바이오로직스의 영업이익", "삼성바이오로직스"),
    ("금호석유화학은 어떤 회사인가", "금호석유화학"),
    ("메리츠금융지주의 배당", "메리츠금융지주"),
]


@pytest.mark.parametrize("q,expected", DETECT)
def test_detects_company_shaped_token(q, expected):
    assert detect_company_mention(q) == expected


# ── 형태 감지: 잡으면 안 되는 것 (오탐 = 정상 질의가 잘못 기권한다) ──────────
#    🔴 이쪽이 본 변경의 위험 축이다. 놓치는 건 종전 동작으로 떨어질 뿐이지만
#       잘못 잡으면 기업이 없는 질의에 "해당 기업을 확인할 수 없습니다"가 나간다.
NO_DETECT = [
    "2024년 매출액 상위 기업은?",
    "전자공시시스템에서 조회 가능한가?",
    "반도체 업황은 어떤가",
    "부채비율 변화를 알려줘",
    "연구개발비는 매출 대비 몇 퍼센트인가",
    "주요 위험요인은 무엇인가",
    "사업보고서상 주요 사업 부문을 설명해줘",
    "영업이익률이 가장 높은 곳은?",
    "안녕하세요",
]


@pytest.mark.parametrize("q", NO_DETECT)
def test_no_false_positive(q):
    assert detect_company_mention(q) is None, f"오탐: {q!r}"


# ── decide() 라우팅 ─────────────────────────────────────────────────────────
def _decide(**kw):
    base = dict(
        question="LG화학의 주요 위험요인은 무엇인가?",
        corp_codes=[], years=[], metric_key=None, has_facts=False,
        top_search_score=0.9, threshold=0.35,
    )
    base.update(kw)
    return decide(**base)


def test_unknown_company_routes_to_out_of_universe():
    """종전엔 ambiguous로 떨어졌다 — mentions_company가 False였기 때문."""
    ab = _decide(mentions_company=True, unknown_company="LG화학")
    assert ab is not None
    assert ab.reason == "out_of_universe"
    assert "LG화학" in ab.message, "무엇을 못 찾았는지 되짚어야 오탈자와 미보유가 구분된다"


def test_out_of_universe_carries_available_facts():
    """AC-AB2: 거부하고 끝내지 않는다. 종전엔 이 경로만 facts가 비어 있었다."""
    ab = _decide(
        mentions_company=True, unknown_company="LG화학",
        available_facts=["LG에너지솔루션 2024년 매출액 25조원"],
    )
    assert ab.available_facts, "out_of_universe 경로 available_facts 누락 회귀"
    assert "LG에너지솔루션 2024년 매출액 25조원" in ab.render()


def test_suggests_similar_companies():
    ab = _decide(
        mentions_company=True, unknown_company="LG화학",
        similar_companies=["LG에너지솔루션", "LG생활건강"],
    )
    body = ab.render()
    assert "LG에너지솔루션" in body and "LG생활건강" in body


def test_ambiguous_still_fires_without_company_token():
    """기업 형태가 없으면 종전대로 ambiguous — 과잉 라우팅 방지."""
    ab = _decide(question="2024년 매출액 상위 기업은?", mentions_company=False)
    assert ab is not None and ab.reason == "ambiguous"


def test_known_company_unaffected():
    """유니버스 안 기업은 corp_codes가 채워지므로 이 경로를 타지 않는다."""
    assert _decide(corp_codes=["00126380"], mentions_company=True,
                   has_facts=True) is None


# ── 소유격 주체 감지 (2026-09-02, Gold Set ABS-007/009 실측) ────────────────
#    형태 사전으로 원리적으로 못 잡는 것들:
#      "△△전자의 …"  익명화 기호 — △는 한글도 영문도 아니다
#      "애플의 …"     2음절 외국 기업명 — 접미사가 없다
#    둘 다 ambiguous로 기권했다. 기업을 지목했는데 되물은 것이라 정보한계 대응 감점.
from dart_agent.store.alias import detect_possessive_subject


@pytest.mark.parametrize("q,expected", [
    ("△△전자의 2024년 매출액은?", "△△전자"),
    ("애플의 2024년 매출액은?", "애플"),
    ("존재하지않는기업㈜의 영업이익은?", "존재하지않는기업㈜"),
    ("테슬라의 2024년 영업이익은?", "테슬라"),
])
def test_possessive_subject_detected(q, expected):
    assert detect_possessive_subject(q) == expected


@pytest.mark.parametrize("q", [
    "회사의 2024년 매출액은?",          # 일반명사
    "우리 기업의 매출 추이는?",
    "상장기업의 평균 부채비율은?",
    "2024년 매출액 상위 기업은?",       # 소유격 자체가 없다
    "매출액이 가장 큰 곳은?",
    "해당 기업의 실적은?",
])
def test_possessive_subject_ignores_common_nouns(q):
    assert detect_possessive_subject(q) is None, q


def test_out_of_universe_requires_specific_mention():
    """🔴 규칙 3은 `mentions_company`가 아니라 `unknown_company`로 발동한다.

    전자는 "기업|회사|사|㈜" 정규식이라 일반명사에도 켜져서, 지목한 적 없는
    기업을 "확인할 수 없습니다"라고 답했다 (2026-09-02 실측).
    """
    ab = _decide(question="2024년 매출액 상위 기업은?",
                 mentions_company=True, unknown_company=None)
    assert ab is None or ab.reason != "out_of_universe"
