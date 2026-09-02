"""서술 계층 수치 게이트 — 요약형 질의에서 구조적으로 항상 실패하던 문제.

실측 (2026-09-02, NCP 리허설 서버 think_trace):
  "현대자동차의 최대주주는?"       → 수치 불일치(누락 9)  → 표 덤프 536자
  "포스코홀딩스 부채비율 변화는?"  → 수치 불일치(누락 34) → 표 덤프 931자
  "카카오 주요 사업 부문 설명해줘" → 수치 불일치(누락 20) → 표 덤프 894자

섹션·시계열 질의의 결정론 본문은 공시 표 원문을 그대로 담아 수치가 수십 개다.
요약은 본래 그 대부분을 버리므로 누락이 반드시 나오고, 서술이 매번 폐기돼
표 원문이 그대로 답변으로 나갔다. "누락 = 위험"은 수치가 곧 답인 질의에서만 참이다.

⚠️ 완화 대상은 **누락뿐**이다. 없던 숫자(환각)는 양쪽 모두 계속 차단한다.
"""

from __future__ import annotations

from dart_agent.agent.narrate import _content_words, _new_content, narrate


class _Resp:
    def __init__(self, content):
        self.content, self.usable, self.usage = content, True, {}


class _LLM:
    name = "fake"

    def __init__(self, out):
        self._out = out

    def chat(self, *_a, **_kw):
        return _Resp(self._out)


TABLE_BODY = (
    "POSCO홀딩스 사업보고서 (2023.12) 1. 요약재무정보: 자산총계 89,900,000백만원, "
    "부채총계 35,100,000백만원, 자본총계 54,800,000백만원, 매출액 77,127,000백만원, "
    "영업이익 3,531,000백만원 [C1]"
)
SUMMARY = "POSCO홀딩스의 2023년 자산총계는 89,900,000백만원입니다 [C1]."


def test_summary_query_accepts_number_drop():
    """요약형: 수치를 하나라도 남기면 채택한다."""
    out, why = narrate(_LLM(SUMMARY), TABLE_BODY, question="부채비율 변화는?",
                       allow_number_drop=True)
    assert out == SUMMARY, why
    assert "LLM 서술 적용" in why


def test_numeric_query_still_rejects_number_drop():
    """수치형: 종전 그대로 누락을 막는다 — 회귀 금지 축."""
    out, why = narrate(_LLM(SUMMARY), TABLE_BODY, question="부채비율 변화는?",
                       allow_number_drop=False)
    assert out == TABLE_BODY
    assert "수치 불일치" in why


def test_summary_query_still_rejects_added_number():
    """완화 대상은 누락뿐 — 없던 숫자(환각)는 요약형에서도 차단한다."""
    hallucinated = "POSCO홀딩스의 부채비율은 64.1%입니다 [C1]."
    out, why = narrate(_LLM(hallucinated), TABLE_BODY, question="부채비율 변화는?",
                       allow_number_drop=True)
    assert out == TABLE_BODY
    assert "수치 불일치" in why


def test_summary_query_rejects_total_number_loss():
    """수치가 전부 사라지면 요약이 아니라 회피다."""
    vague = "POSCO홀딩스의 재무 상태에 관한 내용입니다 [C1]."
    out, why = narrate(_LLM(vague), TABLE_BODY, question="부채비율 변화는?",
                       allow_number_drop=True)
    assert out == TABLE_BODY
    assert "수치 불일치" in why


def test_prose_body_without_numbers_passes():
    """수치가 원래 없던 본문은 완화와 무관하게 통과한다."""
    body = "카카오는 플랫폼 부문과 콘텐츠 부문으로 사업을 구분합니다 [C1]."
    out, why = narrate(_LLM("카카오의 사업은 플랫폼과 콘텐츠로 나뉩니다 [C1]."),
                       body, question="주요 사업 부문은?", allow_number_drop=True)
    assert "LLM 서술 적용" in why, why


# ── 기능어 어간 오탐 (실측: "미근거 내용 주입(구체,규칙,그러)") ──────────────
def test_multisyllable_function_words_are_not_new_content():
    """어간 근사가 2음절이라 3음절 기능어가 집합에 닿지 않던 문제."""
    for w in ("그러나", "따라서", "그래서", "아울러", "구체적으로"):
        assert not _content_words(w), f"{w} → {_content_words(w)}"


def test_narration_connectives_do_not_trigger_injection_gate():
    body = "매출액은 300,870,903백만원입니다 [C1]."
    out = "매출액은 300,870,903백만원입니다. 그러나 구체적으로 보면 그래서 [C1]."
    assert not _new_content(body, out), _new_content(body, out)


def test_real_new_claim_still_detected():
    """완화가 실제 주장 주입까지 풀어주면 안 된다."""
    body = "매출액은 300,870,903백만원입니다 [C1]."
    out = "매출액은 300,870,903백만원입니다. 회계 감사에서 지적을 받았습니다 [C1]."
    assert _new_content(body, out), "없던 주장 탐지 회귀"


# ── 미근거 주입 게이트: 위험어 기반 판정 (2026-09-02 정정) ──────────────────
def test_risky_claim_blocked_even_when_few():
    """부정적 사건 주장은 개수와 무관하게 차단한다 — 종전보다 강한 보장."""
    body = "매출액은 300,870,903백만원입니다 [C1]."
    for claim in ("회계 감사에서 지적을 받았습니다",
                  "소송이 제기되었습니다",
                  "과징금이 부과되었습니다",
                  "상장폐지 사유가 발생했습니다"):
        out = f"매출액은 300,870,903백만원입니다. {claim} [C1]."
        assert _new_content(body, out), f"위험 주장 미탐지: {claim}"


def test_ordinary_rewording_allowed():
    """실측된 오탐 — 전부 문장을 잇는 일반 어휘이지 주장이 아니다."""
    body = "질의와 관련된 공시 구간입니다 [C1]."
    for out in ("질의와 관련된 공시 구간을 검색했습니다. 아래 결과를 참고해 주세요 [C1].",
                "해당 금액의 비율은 공시 범위 내에 있습니다 [C1].",
                "설명은 생략하고 핵심만 정리하면 다음과 같습니다 [C1]."):
        assert not _new_content(body, out), f"오탐: {_new_content(body, out)}"


def test_many_new_words_still_blocked():
    """위험어가 없어도 새 어휘가 지나치게 많으면 다시 쓰기가 아니다."""
    body = "매출액은 300,870,903백만원입니다 [C1]."
    out = ("매출액은 300,870,903백만원입니다. 중국 시장 진출 계획과 신규 공장 착공 "
           "일정 그리고 자회사 편입 절차를 함께 추진합니다 [C1].")
    assert _new_content(body, out), "과다 주입 미탐지"
