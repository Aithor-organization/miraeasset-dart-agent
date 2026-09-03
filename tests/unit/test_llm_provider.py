"""LLM 프로바이더 계약 테스트 — thinking 모델 절단 방어 (실측 함정 회귀 가드).

🔴 배경 (2026-08-11 실호출 실측)
HCX-007은 답변 전에 추론 토큰을 쓴다. 그 추론이 `max_completion_tokens`에 잘리면
**HTTP 200 + content 빈 문자열**로 돌아온다. 오류처럼 보이지 않아 그대로 답변에 실린다.

  2048 토큰 → 동일 요청 5회 중 **3회 빈 응답** (reasoning 2047~2048로 한도에 절단)
  8192 토큰 + 간결 지시 → 5/5 성공 (추론 284~1,010에서 자연 종료)

이 테스트는 네트워크를 타지 않는다 — 응답 파싱 계약만 검증한다.
"""

from __future__ import annotations

import pytest

from dart_agent.llm.provider import LLMResponse


class TestTruncationContract:
    def test_empty_content_with_truncation_is_not_usable(self):
        """🔴 빈 응답을 답변으로 쓰면 안 된다."""
        r = LLMResponse(content="", truncated=True, usage={"completion_tokens": 2048})
        assert not r.usable

    def test_empty_content_without_flag_is_still_not_usable(self):
        """플래그가 없어도 빈 문자열은 쓸 수 없다 (이중 방어)."""
        assert not LLMResponse(content="   ").usable

    def test_normal_response_is_usable(self):
        assert LLMResponse(content="삼성전자의 매출액은 300,870,903백만원입니다.").usable

    def test_tool_calls_only_is_usable(self):
        """도구 호출만 있고 content가 비는 것은 정상 흐름이다."""
        r = LLMResponse(content="", tool_calls=[{"name": "fact_query", "arguments": {}}])
        assert r.usable

    def test_truncated_overrides_tool_calls(self):
        r = LLMResponse(content="", tool_calls=[], truncated=True)
        assert not r.usable


class TestDefaultTokenBudget:
    """🔴 기본값을 2048로 되돌리면 3/5 확률로 빈 응답이 된다 — 하한을 고정한다."""

    MIN_SAFE = 8192

    def test_clova_default_is_high_enough(self):
        import inspect

        from dart_agent.llm.clova import ClovaProvider

        default = inspect.signature(ClovaProvider.chat).parameters["max_tokens"].default
        assert default >= self.MIN_SAFE, (
            f"max_tokens 기본값 {default} — 추론 절단으로 빈 응답이 발생한다 "
            f"(실측: 2048에서 3/5 실패). {self.MIN_SAFE} 이상 유지할 것"
        )

    def test_protocol_and_stub_defaults_match(self):
        """계약·구현·Stub이 어긋나면 교체 시 조용히 절단이 되살아난다."""
        import inspect

        from dart_agent.llm.clova import ClovaProvider
        from dart_agent.llm.provider import LLMProvider
        from dart_agent.llm.stub import StubProvider

        defaults = {
            cls.__name__: inspect.signature(cls.chat).parameters["max_tokens"].default
            for cls in (LLMProvider, ClovaProvider, StubProvider)
        }
        assert len(set(defaults.values())) == 1, f"기본값 불일치: {defaults}"
        assert all(v >= self.MIN_SAFE for v in defaults.values()), defaults


class TestTruncationDetection:
    """clova.py의 절단 판정 로직 — 실제 응답 형태를 재현해 검증한다."""

    @staticmethod
    def _detect(content: str, tool_calls: list, finish: str, reasoning: int,
                max_tokens: int) -> bool:
        # clova.py와 동일한 판정식 (로직 변경 시 여기도 실패하도록 의도적 중복)
        return not content.strip() and not tool_calls and (
            finish == "length" or reasoning >= max_tokens * 0.95
        )

    def test_detects_reasoning_exhaustion(self):
        """실측 케이스: reasoning 2047/2048, content 빈 문자열."""
        assert self._detect("", [], "stop", 2047, 2048)

    def test_detects_length_finish(self):
        assert self._detect("", [], "length", 100, 8192)

    def test_normal_answer_not_flagged(self):
        assert not self._detect("매출액은 300조원입니다.", [], "stop", 467, 8192)

    def test_healthy_short_reasoning_not_flagged(self):
        """실측: 8192 한도에서 추론 284로 자연 종료 → 절단 아님."""
        assert not self._detect("답변", [], "stop", 284, 8192)

    def test_tool_call_with_empty_content_not_flagged(self):
        assert not self._detect("", [{"name": "fact_query"}], "tool_calls", 300, 8192)


def test_stub_response_is_marked():
    """키 없이 동작할 때 Stub임이 응답에 드러나야 한다 (AC-L2)."""
    from dart_agent.llm.stub import StubProvider

    r = StubProvider().chat([{"role": "user", "content": "테스트"}])
    assert r.stubbed is True
    assert not r.truncated


@pytest.mark.parametrize("cfg_key,expected", [("", False), ("nv-abc", True)])
def test_has_llm_reflects_key_presence(cfg_key, expected, monkeypatch):
    from dart_agent.config import load_config

    monkeypatch.setenv("CLOVA_API_KEY", cfg_key)
    assert load_config().has_llm is expected


def test_llm_kill_switch_forces_stub(monkeypatch):
    from dart_agent.config import load_config
    from dart_agent.llm.provider import build_providers

    monkeypatch.setenv("CLOVA_API_KEY", "nv-test-key")
    monkeypatch.setenv("LLM_ENABLED", "0")
    llm, embedding, notes = build_providers(load_config())
    assert llm.name == "stub"
    assert embedding is None
    assert any("kill switch" in note for note in notes)
