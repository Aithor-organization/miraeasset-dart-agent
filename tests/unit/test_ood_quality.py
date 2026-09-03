"""OOD(골드셋 밖) 질의 3종 결함 — 2026-09-03 실측 후 회귀 방지.

주최측 참고용 질의 set(공고 9p)은 난이도 상/중/하 × **Closed/Open-ended 혼합**이고,
Open 예시로 "주요 투자 계획을 **정리해줘**"·"핵심 사업이 어떻게 변화했는지 **설명해줘**",
비교 예시로 "A와 B 중 … **더 큰 기업은?**"을 명시한다. 골드셋 186문항은 수치 대조형에
치우쳐(115/186) 이 축의 커버가 얇았고, 실제로 세 가지가 무너져 있었다:

  1. "삼성전자 실적 요약해줘"          → 요약재무정보 **표 원문 400자** 덤프
  2. "현대차와 기아 중 어디가 더 성장?" → 두 기업 해석됐는데 **기아만** 답변
  3. "부채비율이 위험한 수준이야?"      → 기권해야 할 투자 판단에 표를 뱉음
"""

from __future__ import annotations

import pytest

from dart_agent.agent import tabular
from dart_agent.agent.abstention import decide, detect_judgment

# ── ③ 투자·건전성 판정 요구 → forbidden_judgment ────────────────────────────
#    가르는 축은 **서술어 자리의 형용사**다. 명사("위험요인"·"위험관리")는 사실 조회다.
JUDGMENT_YES = [
    "LG에너지솔루션 부채비율이 위험한 수준이야?",
    "삼성전자 재무구조가 안전한가요?",
    "이 회사 괜찮은가?",
    "SK하이닉스 실적이 좋아?",
    "현대차 부채가 과도한 수준인가",
    "네이버 성장성이 우수한가요?",
    "카카오 유동성이 불안한 상태인가",
]
JUDGMENT_NO = [
    "주요 위험요인은 무엇인가?",          # 공시 기재 항목
    "위험관리 정책을 설명해줘",           # 섹션명
    "안전보건 관련 사항을 알려줘",
    "삼성전자 2024년 매출액은?",
    "부채비율을 알려줘",                  # 수치 조회 — 판정이 아니다
    "건전성 감독 규정 관련 기재사항",
    "2024년 영업이익 증감률은?",
    "우량 자산 비중",
]


@pytest.mark.parametrize("q", JUDGMENT_YES)
def test_judgment_detected(q):
    assert detect_judgment(q), f"미탐: {q!r}"


@pytest.mark.parametrize("q", JUDGMENT_NO)
def test_judgment_no_false_positive(q):
    assert not detect_judgment(q), f"오탐(사실 조회를 기권시킴): {q!r}"


def _decide(**kw):
    base = dict(question="", corp_codes=[], years=[], metric_key=None,
                has_facts=True, top_search_score=0.9, threshold=0.35)
    base.update(kw)
    return decide(**base)


def test_judgment_routes_to_forbidden_judgment():
    ab = _decide(question="LG에너지솔루션 부채비율이 위험한 수준이야?",
                 corp_codes=["00126380"], mentions_company=True)
    assert ab is not None and ab.reason == "forbidden_judgment"


def test_judgment_carries_facts_and_followup():
    """AC-AB2 — 거부하고 끝내지 않는다."""
    ab = _decide(question="재무구조가 안전한가요?",
                 available_facts=["삼성전자 2024년 매출액 300조원"])
    body = ab.render()
    assert "300조원" in body and "?" in body


# ── ② 비교 축 미특정 → 역질문 (한쪽만 답하지 않는다) ────────────────────────
def test_comparison_without_metric_asks_back():
    ab = _decide(question="현대차와 기아 중 어디가 더 성장했어?",
                 corp_codes=["00164742", "00106641"], is_comparison=True,
                 mentions_company=True)
    assert ab is not None and ab.reason == "no_comparison_metric"
    assert "지표" in ab.render()


def test_comparison_with_metric_proceeds():
    """🔴 골드셋 비교계열 75건은 전부 지표가 특정된다 — 이 경로는 막히면 안 된다."""
    assert _decide(question="현대차와 기아 중 2024년 매출액이 더 큰 기업은?",
                   corp_codes=["00164742", "00106641"], metric_key="revenue",
                   is_comparison=True, mentions_company=True) is None


def test_single_company_not_affected():
    """기업이 1곳이면 비교가 아니다 — 역질문 대상이 아니다."""
    assert _decide(question="삼성전자 사업 개요를 알려줘",
                   corp_codes=["00126380"], is_comparison=True,
                   mentions_company=True) is None


# ── ① 표 원문 → 서술 ────────────────────────────────────────────────────────
#    🔴 DB의 section.text는 **개행이 0개인 단일 라인**이다(실측 2,750자 / 줄바꿈 0).
#       아래 픽스처도 그 형태를 그대로 재현한다 — 줄 단위 파서를 다시 쓰면 여기서 깨진다.
FLAT_TABLE = (
    "가. 요약연결재무정보 (단위 : 백만원) 구 분 제55기 1분기 제54기 제53기 "
    "[유동자산] 214,442,141 218,470,581 218,163,185 "
    "ㆍ현금및현금성자산 72,949,377 49,680,710 39,031,415 "
    "자산총계 454,091,777 448,424,507 426,621,158 "
    "[유동부채] 76,057,448 78,344,852 88,117,133 "
    "부채총계 94,292,361 93,674,903 121,721,227 "
    "매출액 63,745,371 302,231,360 279,604,799 "
    "영업이익 640,178 43,376,630 51,633,856 "
    "당기순이익 1,574,600 55,654,077 39,907,450"
)
PIPE_TABLE = (
    "| (단위 : 백만원) | | 구 분 | 제58기 | 제57기 | "
    "| [유동자산] | 306,220,075 | 247,684,612 | "
    "| 자산총계 | 633,339,604 | 514,531,948 | "
    "| 매출액 | 133,873,444 | 300,870,903 |"
)


def test_looks_tabular():
    assert tabular.looks_tabular(FLAT_TABLE)
    assert tabular.looks_tabular(PIPE_TABLE)
    assert not tabular.looks_tabular("당사는 반도체를 제조합니다.")
    assert not tabular.looks_tabular("")


def test_summarize_prioritizes_income_statement():
    """🔴 등장 순서로 자르면 재무상태표가 6칸을 다 먹고 손익이 사라진다.

    "실적 요약"의 핵심은 매출액·영업이익·당기순이익이므로 이들이 앞에 와야 한다.
    """
    out = tabular.summarize(FLAT_TABLE, "삼성전자 요약재무정보")
    assert out is not None
    for k in ("매출액", "영업이익", "당기순이익"):
        assert k in out
    assert out.index("매출액") < out.index("자산총계")
    assert "단위: 백만원" in out


def test_summarize_takes_first_column_only():
    """여러 기수가 있으면 **첫 값**(당기)만 쓴다 — 전기 값이 섞이면 오답이 된다."""
    out = tabular.summarize(FLAT_TABLE, "X")
    assert "63,745,371" in out          # 당기 매출액
    assert "302,231,360" not in out     # 전기 매출액


def test_summarize_never_invents_numbers():
    """D1 — 값은 원문에 있는 문자열이어야 한다 (재계산·반올림 금지)."""
    import re
    out = tabular.summarize(FLAT_TABLE, "X")
    for n in re.findall(r"\d{1,3}(?:,\d{3})+", out):
        assert n in FLAT_TABLE, f"원문에 없는 수치 생성: {n}"


def test_summarize_handles_pipe_table():
    out = tabular.summarize(PIPE_TABLE, "삼성전자")
    assert out and "133,873,444" in out and "633,339,604" in out


def test_summarize_returns_none_when_unextractable():
    """뽑을 항목이 2개 미만이면 None — 호출자가 원문 절단으로 폴백한다.

    잘못 읽은 요약을 내보내느니 표를 그대로 보여주는 편이 낫다.
    """
    assert tabular.summarize("| 구 분 | 금액 | | 기타 | 1,234 |", "X") is None
    assert tabular.summarize("서술만 있고 표가 아닙니다.", "X") is None
