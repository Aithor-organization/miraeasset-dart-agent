"""🔴 `retrieved_context` PII 마스킹 회귀 가드 (2026-08-19).

AITHOR `security-engineer` 지적 + 실측 확인:

    답변(`answer`)은 마스킹되는데 **근거 본문(`retrieved_context`)은 평문**이었다.

공격 경로가 우회적이라 더 위험하다 — PII 질의 정규식(`pii.is_pii_request`)은
"생년월일"·"성별" 같은 키워드를 본다. "삼성전자 임원 및 직원 현황"처럼
**중립적 문장**으로 VIII-1 섹션을 부르면 게이트를 통과하고, 답변에는 안 나와도
근거 본문 1,200자에 그대로 실린다. `retrieved_context`는 채점 대상 산출물이다.
"""

from dart_agent.agent import pii


class TestMaskAlways:
    """섹션과 무관하게 지워야 하는 식별자 — 회계기간과 형태가 겹치지 않는다."""

    def test_rrn_masked(self):
        assert "900101-1234567" not in pii.mask_always("주민 900101-1234567 확인")

    def test_email_masked(self):
        assert "hong@x.com" not in pii.mask_always("담당 hong@x.com 문의")

    def test_phone_masked(self):
        assert "010-1234-5678" not in pii.mask_always("연락처 010-1234-5678")

    def test_accounting_period_survives(self):
        """🔴 `_BIRTH`를 전역 적용하면 안 되는 이유 — 회계기간이 지워진다.

        `mask_always`는 그래서 생년월일을 건드리지 않는다.
        """
        t = "2024년 12월 31일 기준 매출액은 300,870,903백만원입니다."
        assert pii.mask_always(t) == t

    def test_empty_safe(self):
        assert pii.mask_always("") == ""


class TestMaskFull:
    """PII 섹션 한정 — 생년월일까지 지운다."""

    def test_birth_masked(self):
        assert "1965년 03월" not in pii.mask("홍길동 1965년 03월생")

    def test_section_detection_drives_scope(self):
        assert pii.is_pii_section("VIII-1", "1. 임원 및 직원 등의 현황")
        assert not pii.is_pii_section("III-2-2", "연결 손익계산서")


class TestNeutralQuestionBypass:
    """🔴 이 우회가 이 결함의 핵심이다 — 게이트를 통과하는 중립적 질문."""

    def test_neutral_question_passes_pii_gate(self):
        """PII 게이트는 이 질문을 막지 않는다 (막아서도 안 된다 — 과잉 차단)."""
        assert not pii.is_pii_request("삼성전자 임원 및 직원 현황")
        assert not pii.is_pii_request("임원 수는 몇 명인가")

    def test_but_section_is_pii_so_context_must_mask(self):
        """게이트를 통과해도 **섹션이 PII면 근거 본문은 마스킹돼야 한다.**"""
        raw = "홍길동 1965년 03월생 010-1234-5678 hong@x.com"
        masked = pii.mask_always(raw)
        if pii.is_pii_section("VIII-1", "1. 임원 및 직원 등의 현황"):
            masked = pii.mask(masked)
        for leak in ("1965년 03월", "010-1234-5678", "hong@x.com"):
            assert leak not in masked, f"{leak} 유출"
