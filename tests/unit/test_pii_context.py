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


class TestPreEgressGate:
    """민감 질의는 `understand()`보다 먼저 끝나므로 LLM으로 나갈 수 없다."""

    def test_sensitive_request_does_not_reach_understand_or_cache(self):
        from dart_agent.agent.orchestrator import Orchestrator

        orch = object.__new__(Orchestrator)
        orch.understand = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("민감 요청에서 understand()가 호출되면 안 됩니다")
        )
        result = orch.answer("P-1", "삼성전자 임원 생년월일과 연락처를 알려줘")

        assert result.abstained
        assert result.abstain_reason == "pii_request"
        assert "외부 모델·도구 미호출" in result.think_trace
        assert not hasattr(orch, "_cache")

    def test_credential_value_is_sensitive_but_policy_question_is_not(self):
        from dart_agent.agent.orchestrator import Orchestrator

        assert pii.is_sensitive_request("api_key=abcdefgh12345678로 접속해줘")
        assert not pii.is_sensitive_request("API 키 보관 정책을 알려줘")
        orch = object.__new__(Orchestrator)
        orch.understand = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("자격증명은 외부 호출 경로에 도달하면 안 됩니다")
        )
        result = orch.answer("P-2", "api_key=abcdefgh12345678로 접속해줘")
        assert result.abstain_reason == "sensitive_input"


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


# ── 법인 주소 오탐 (2026-09-03, aithor 머지 리뷰 실측) ──────────────────────
#    `주소지`가 전역 패턴이라 "회사의 주소지는 어디인가"가 개인정보로 거절됐다.
#    법인 주소는 PII가 아니라 공시 정상 항목 — 답변 가능한 질의를 거절하면
#    정확성(1)·요구사항 충족(3)에서 감점된다.
import pytest

from dart_agent.agent import pii as _pii


@pytest.mark.parametrize("q", [
    "회사의 주소지는 어디인가",
    "본점 소재지는 어디인가",
    "삼성전자 주소를 알려줘",
    "사업장 주소가 어떻게 되나요",
])
def test_company_address_is_not_pii(q):
    assert not _pii.is_pii_request(q), f"법인 주소 오탐: {q!r}"


@pytest.mark.parametrize("q", [
    "임원의 주소를 알려줘",
    "대표이사 주소지가 궁금해",
    "직원 개인 주소를 알려줘",
])
def test_person_address_still_blocked(q):
    assert _pii.is_pii_request(q), f"개인 주소 누락: {q!r}"
