"""🔴 failure 케이스 — 외부 도구가 죽었을 때의 거동 (2026-08-19 신설).

AITHOR `verifier-qa` 지적: 프레임워크 골든 케이스 5종 중 **failure가 0건**이었다.
`abstain` 15건은 전부 *정책적* 거부(투자의견 요구)이고 *장애* 케이스는 없었다.

    "골든 케이스 failure[권장] — 외부 도구가 죽었을 때 어떻게 되는가"

이 시스템의 외부 의존은 **HCX 하나**다. 그 하나가 죽는 방식을 전부 시험한다.
공통 계약: 어떤 실패에서도 **답변은 나오고, 정확도는 유지되며, 500이 아니다.**
"""

import time

import pytest

from dart_agent.agent.narrate import narrate
from dart_agent.agent.route_section import route

BODY = "삼성전자의 2024년 연결기준 매출액은 300,870,903백만원(300.9조원)입니다. [C1]"
Q = "삼성전자의 2024년 연결기준 매출액은?"


class _Dead:
    """지정한 예외를 던지는 LLM."""

    name = "clova"

    def __init__(self, exc):
        self._exc = exc

    def chat(self, *a, **k):
        raise self._exc


class TestNarrateSurvivesEveryFailure:
    """서술 계층이 죽어도 **결정론 본문이 그대로 나간다**."""

    @pytest.mark.parametrize("exc,label", [
        (RuntimeError("clova 429: rate exceeded"), "쿼터 소진"),
        (RuntimeError("clova 401: invalid key"), "키 만료"),
        (RuntimeError("clova 500: internal"), "HCX 서버 장애"),
        (RuntimeError("clova transport error: timeout"), "네트워크 타임아웃"),
        (ConnectionError("DNS 실패"), "DNS 실패"),
        (MemoryError("메모리 부족"), "메모리 부족"),
    ])
    def test_body_preserved(self, exc, label):
        text, why = narrate(_Dead(exc), BODY, question=Q)
        assert text == BODY, f"{label}에서 본문이 손상됨"
        assert why, "사유가 비었다 — 조용한 폴백은 장애 은폐다"

    def test_deadline_exceeded_is_graceful(self):
        """예산 소진도 장애가 아니라 **강등**이다."""
        from dart_agent.llm.ratelimit import DeadlineExceeded
        text, why = narrate(_Dead(DeadlineExceeded("잔여 0.1s")), BODY, question=Q)
        assert text == BODY and "오류" in why

    def test_empty_response_degrades(self):
        """🔴 HCX-007 추론 절단 — HTTP 200인데 본문이 빈다."""
        class Empty:
            name = "clova"
            def chat(self, *a, **k):
                class R:
                    content, tool_calls, usage, truncated = "", [], {}, True
                    usable = False
                return R()
        text, why = narrate(Empty(), BODY, question=Q)
        assert text == BODY and "강등" in why


class TestRouteSurvivesEveryFailure:
    """목차 라우팅이 죽으면 **검색 경로로 떨어진다** (빈 리스트)."""

    @pytest.mark.parametrize("exc", [
        RuntimeError("clova 429"), ConnectionError("네트워크"), TimeoutError("타임아웃"),
    ])
    def test_returns_empty_not_raise(self, exc):
        paths, why = route(_Dead(exc), "배당에 관한 사항은?")
        assert paths == [], "실패 시 주소를 반환하면 안 된다"
        assert why, "사유 없음"

    def test_garbage_response_dropped(self):
        """LLM이 쓰레기를 뱉어도 화이트리스트가 막는다."""
        class Garbage:
            name = "clova"
            def chat(self, *a, **k):
                class R:
                    content = "ZZ-999, 서버 오류, <html>500</html>"
                    tool_calls, usage, truncated, usable = [], {}, False, True
                return R()
        assert route(Garbage(), "배당?")[0] == []


class TestRateLimitDegradesGracefully:
    """유입 제한도 계약을 지킨다 — 429여도 4필드가 나온다."""

    def test_429_keeps_contract_fields(self, monkeypatch):
        from dart_agent.api import ratelimit_mw

        class Req:
            headers = {"x-forwarded-for": "1.2.3.4"}
            query_params = {"question_id": "Q1", "question": "매출액은?"}
            class client:
                host = "1.2.3.4"

        monkeypatch.setattr(ratelimit_mw, "_LIMIT", 1)
        monkeypatch.setattr(ratelimit_mw, "_hits", __import__("collections").defaultdict(
            __import__("collections").deque))
        assert ratelimit_mw.check(Req()) is None          # 1회차 통과
        blocked = ratelimit_mw.check(Req())               # 2회차 차단
        assert blocked is not None and blocked.status_code == 429
        import json
        payload = json.loads(bytes(blocked.body).decode())
        for f in ("question_id", "question", "retrieved_context", "think_trace", "answer"):
            assert f in payload, f"429 응답에 계약 필드 {f} 누락"
        assert payload["abstained"] is True

    def test_disabled_when_zero(self, monkeypatch):
        from dart_agent.api import ratelimit_mw
        monkeypatch.setattr(ratelimit_mw, "_LIMIT", 0)
        class Req:
            headers = {}
            query_params = {}
            class client:
                host = "9.9.9.9"
        assert ratelimit_mw.check(Req()) is None


class TestCountersObserveDegradation:
    """🔴 강등이 **관측 가능**해야 한다 — RUNBOOK 감시 지시의 전제."""

    def test_counter_increments(self):
        from dart_agent import counters
        counters.reset()
        counters.bump("llm_degraded")
        counters.bump("llm_degraded")
        assert counters.snapshot()["llm_degraded"] == 2

    def test_snapshot_is_a_copy(self):
        from dart_agent import counters
        counters.reset()
        counters.bump("x")
        snap = counters.snapshot()
        counters.bump("x")
        assert snap["x"] == 1, "스냅샷이 살아있는 참조다"
