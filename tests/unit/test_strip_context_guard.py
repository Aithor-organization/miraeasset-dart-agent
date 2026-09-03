"""제거기(strip)가 판정기(verify)보다 엄격하면 안 된다.

실측 결함 (2026-09-03, aithor 머지 리뷰):
  UNSUPPORTED_ASSERTION_TERMS는 verify()에서 **문맥에 없을 때만** 위반이다.
  그런데 strip_failing_sentences는 그 조건 없이 무조건 문장을 지웠다.
  rep.ok가 False이기만 하면 사유와 무관하게 strip이 돌기 때문에,
  다른 문장의 V1 위반 하나가 근거 있는 "부진" 문장까지 날렸다.

  근거 있는 서술 삭제는 정확성(1)·근거 완전성(2)·요구사항 충족(3)을 동시에 깎는다.
"""

from __future__ import annotations

from dart_agent.agent.verifier import strip_failing_sentences, verify

_CTX = "매출액 1,000억원[C1]. 전년 대비 실적이 부진하였습니다.[C1]"
_BODY = (
    "2024년 매출액은 1,000억원입니다[C1]. "
    "전년 대비 실적이 부진하였습니다[C1]. "
    "기타 항목은 9,999억원입니다[C1]."
)


def _verify(body: str, ctx: str):
    return verify(body, context=ctx, citation_ids={"C1"}, requirements=[],
                  grounded_values={"1,000억원", "1000"})


def test_verify_does_not_flag_context_backed_assertion():
    """전제 확인 — 문맥에 "부진"이 있으면 V4는 발동하지 않는다."""
    rep = _verify(_BODY, _CTX)
    assert rep.v4_forbidden == []
    assert rep.v1_ungrounded_numbers, "V1이 걸려야 strip 경로를 탄다"


def test_strip_keeps_context_backed_assertion():
    """🔴 회귀 핀 — 다른 문장의 V1 위반 때문에 근거 있는 서술이 사라지면 안 된다."""
    rep = _verify(_BODY, _CTX)
    cleaned = strip_failing_sentences(_BODY, rep, context=_CTX)
    assert "부진" in cleaned, "문맥에 근거가 있는 서술이 삭제됐다"
    assert "9,999억원" not in cleaned, "근거 없는 수치는 여전히 제거돼야 한다"


def test_strip_removes_assertion_absent_from_context():
    """반대 방향 — 문맥에 없는 단정은 종전대로 제거한다."""
    ctx = "매출액 1,000억원[C1]."
    body = "2024년 매출액은 1,000억원입니다[C1]. 수익성이 개선되었습니다[C1]."
    rep = _verify(body, ctx)
    assert "근거 없는 성과/추세 단정" in rep.v4_forbidden
    cleaned = strip_failing_sentences(body, rep, context=ctx)
    assert "개선" not in cleaned


def test_forbidden_patterns_unaffected_by_context():
    """투자의견·미래예측은 문맥에 있어도 금지 — 문맥 가드가 적용되면 안 된다."""
    ctx = "목표주가 100,000원으로 제시한다[C1]."
    body = "삼성전자의 목표 주가는 100,000원입니다[C1]."
    rep = _verify(body, ctx)
    assert "목표주가 제시" in rep.v4_forbidden
    assert strip_failing_sentences(body, rep, context=ctx) == ""
