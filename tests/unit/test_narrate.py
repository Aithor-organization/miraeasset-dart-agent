"""서술 계층 안전장치 회귀 가드 (2026-08-18).

🔴 이 테스트가 지키는 것: **LLM이 답변 품질을 떨어뜨리지 못하게** 하는 것.

실제로 관측된 위반 2건이 여기 고정돼 있다 — 프롬프트로 금지했는데도 나왔다:
  · "300,870,903백만원입니다" → "**으로 예상됩니다**"  (확정 공시를 추측으로 바꿈)
  · 답변 끝에 "(수정 사항: … 변경하여 …)" 편집 후기를 붙임

지시 준수에 기대면 조용히 뚫린다. 그래서 검사가 코드에 있다.
"""

import pytest

from dart_agent.agent.narrate import narrate

BODY = "삼성전자의 2024년 연결기준 매출액은 300,870,903백만원(300.9조원)입니다. [C1]"


class FakeLLM:
    """지정한 문자열을 그대로 뱉는 LLM. 안전장치만 시험한다."""

    name = "fake"

    def __init__(self, out, *, usable=True, raise_exc=None):
        self._out, self._usable, self._exc = out, usable, raise_exc

    def chat(self, messages, **kw):
        if self._exc:
            raise self._exc
        return _Resp(self._out, self._usable)


class _Resp:
    def __init__(self, content, usable):
        self.content, self._usable, self.usage = content, usable, {}
        self.tool_calls, self.truncated = [], not usable

    @property
    def usable(self):
        return self._usable


def _run(out, **kw):
    return narrate(FakeLLM(out, **kw), BODY, question="삼성전자 2024년 매출액은?")


class TestPassThrough:
    def test_stub_keeps_template(self):
        class Stub:
            name = "stub"

        text, why = narrate(Stub(), BODY, question="q")
        assert text == BODY and "stub" in why

    def test_none_llm_keeps_template(self):
        text, _ = narrate(None, BODY, question="q")
        assert text == BODY

    def test_clean_rewrite_is_accepted(self):
        """수치·인용이 보존된 정상 다듬기는 통과해야 한다 (과잉 차단 방지)."""
        good = "삼성전자의 2024년 연결기준 매출액은 300,870,903백만원(300.9조원)으로 집계되었습니다. [C1]"
        text, why = _run(good)
        assert text == good, why


class TestNumberIntegrity:
    def test_rounded_number_rejected(self):
        """🔴 반올림은 D1(수치 결정론) 위반이다."""
        text, why = _run("삼성전자의 2024년 매출액은 약 300조원입니다. [C1]")
        assert text == BODY and "수치" in why

    def test_invented_number_rejected(self):
        text, why = _run("매출액 300,870,903백만원(300.9조원), 영업이익 32,725,961백만원입니다. [C1]")
        assert text == BODY and "수치" in why

    def test_derived_display_may_be_dropped(self):
        """🔴 폴백 최다 원인 — LLM이 `(300.9조원)` 괄호를 떨어뜨린다.

        이미 남아 있는 정확한 값의 환산 표기라 잃어도 거짓이 안 생긴다.
        면제하지 않으면 채택률이 반토막 난다 (실측 62/164).
        """
        out = "삼성전자의 2024년 연결기준 매출액은 300,870,903백만원입니다. [C1]"
        text, why = _run(out)
        assert text == out, why

    def test_rounding_into_derived_slot_still_rejected(self):
        """🔴 면제가 반올림 통로가 되면 안 된다 (실측 8470.7 → 8471)."""
        body = "영업이익은 8,470.7억원입니다. [C1]"
        text, why = narrate(FakeLLM("영업이익은 8,471억원입니다. [C1]"), body, question="q")
        assert text == body and "수치" in why

    def test_unit_swap_rejected(self):
        """🔴 실사고 (골드셋 v2 회귀) — 자릿수는 그대로 두고 **단위만** 바꿨다.

        `17,569,457,486천원` → `17,569,457,486백만원`.
        숫자만 비교하던 검사는 통과시켰고, **1000배 틀린 답**이 채점에 나갔다.
        """
        body = "HD현대중공업의 2025년 별도기준 매출액은 17,569,457,486천원(17.6조원)입니다. [C1]"
        out = "HD현대중공업의 2025년 별도기준 매출액은 17,569,457,486백만원입니다. [C1]"
        text, why = narrate(FakeLLM(out), body, question="q")
        assert text == body, f"단위 변조가 통과됨: {why}"
        assert "수치" in why

    @pytest.mark.parametrize("bad_unit", ["백만원", "억원", "조원", "원"])
    def test_any_unit_swap_rejected(self, bad_unit):
        body = "매출액은 1,234천원입니다. [C1]"
        text, _ = narrate(FakeLLM(f"매출액은 1,234{bad_unit}입니다. [C1]"), body, question="q")
        assert text == body, f"{bad_unit}로의 변조가 통과됨"

    def test_correct_unit_preserved_passes(self):
        """단위를 지키면 통과해야 한다 — 과잉 차단 방지."""
        body = "매출액은 1,234천원입니다. [C1]"
        out = "매출액은 1,234천원으로 집계되었습니다. [C1]"
        text, _ = narrate(FakeLLM(out), body, question="q")
        assert text == out

    def test_ordered_list_markers_are_not_numbers(self):
        """줄머리 `1. 2.`는 서식이지 수치가 아니다 (실측 추가 ['1','2'])."""
        out = ("2024년 매출액입니다. [C1]\n"
               "1. 삼성전자: 300,870,903백만원(300.9조원)\n"
               "2. 근거: 연결 손익계산서")
        text, why = _run(out)
        assert text == out, why

    def test_year_change_is_not_counted_as_number(self):
        """연도·인용은 비교 대상이 아니다 — 서식이라 표현이 달라질 수 있다."""
        text, why = _run("2024년 삼성전자 연결 매출액은 300,870,903백만원(300.9조원)입니다. [C1]")
        assert text != BODY, why


class TestCitationIntegrity:
    def test_dropped_citation_rejected(self):
        text, why = _run("삼성전자의 2024년 매출액은 300,870,903백만원(300.9조원)입니다.")
        assert text == BODY and "인용" in why


class TestHedgeInjection:
    """🔴 실측 위반 — 확정 공시를 추측으로 바꿨다."""

    VIOLATIONS = [
        "삼성전자의 2024년 매출액은 300,870,903백만원(300.9조원)으로 예상됩니다. [C1]",
        "매출액은 300,870,903백만원(300.9조원)으로 추정됩니다. [C1]",
        "매출액은 300,870,903백만원(300.9조원)일 것으로 보입니다. [C1]",
        "매출액은 300,870,903백만원(300.9조원)으로 전망됩니다. [C1]",
    ]

    @pytest.mark.parametrize("out", VIOLATIONS)
    def test_hedge_rejected(self, out):
        text, why = _run(out)
        assert text == BODY, f"추측 표현이 통과됨: {out}"
        assert "추측" in why

    def test_hedge_already_in_original_is_kept(self):
        """원본이 이미 추측을 인용하고 있으면 정당하다 — 과잉 차단 방지.

        공시 원문에 "예상 매출액"이 실제로 기재된 경우가 있다.
        """
        body = "회사가 공시한 예상 매출액은 1,000백만원입니다. [C1]"
        out = "공시된 예상 매출액은 1,000백만원입니다. [C1]"
        text, _ = narrate(FakeLLM(out), body, question="q")
        assert text == out


class TestMetaComment:
    """🔴 실측 위반 — 편집 후기를 답변 본문에 붙였다.

    HCX-007이 프롬프트 금지에도 완강하게 붙인다. 앞쪽 본문은 멀쩡하므로
    **잘라내고 본문을 살린다** — 거부하면 채택률이 0%가 된다 (실측).
    """

    CLEAN = "삼성전자의 2024년 매출액은 300,870,903백만원(300.9조원)입니다. [C1]"

    @pytest.mark.parametrize("tail", [
        "\n\n(수정 사항: '입니다'를 자연스럽게 변경)",
        "\n\n(수정 사항:\n1. 표현 변경\n2. 순서 재배치\n3. 가독성 향상)",
        "\n\n간결성을 위해 문장을 줄였습니다.",
        "\n\n주요 변경: 어미 정리",
        "\n\n- 개선 사항: 조사 수정",
    ])
    def test_meta_tail_is_stripped_and_body_survives(self, tail):
        """🔴 핵심 회귀: 목록 번호(1.2.3.)가 수치 검사를 오염시켜
        정상 서술본이 통째로 버려지던 결함 (채택률 0%)."""
        text, why = _run(self.CLEAN + tail)
        assert text == self.CLEAN, f"본문이 살아남지 못함: {why}"
        assert "수치" not in why, f"메타 번호가 수치로 오인됨: {why}"

    def test_meta_only_response_falls_back(self):
        text, why = _run("\n\n(수정 사항: 없음)")
        assert text == BODY

    def test_inline_meta_still_rejected(self):
        """잘라낼 수 없는 위치(본문 중간)의 메타는 거부한다."""
        text, why = _run("위 답변을 다듬으면 매출액은 300,870,903백만원(300.9조원)입니다. [C1]")
        assert text == BODY and "메타" in why


class TestDegradation:
    """🔴 LLM이 죽어도 답변은 나가야 한다 — 결정론 본문이 이미 손에 있다."""

    def test_api_error_falls_back(self):
        text, why = _run("...", raise_exc=RuntimeError("boom"))
        assert text == BODY and "오류" in why

    def test_truncated_response_falls_back(self):
        """HCX-007 thinking 함정 — 추론만 하고 본문이 비는 경우."""
        text, why = _run("", usable=False)
        assert text == BODY and "강등" in why

    def test_empty_body_skipped(self):
        text, why = narrate(FakeLLM("무엇이든"), "  ", question="q")
        assert text == "  " and "스킵" in why
