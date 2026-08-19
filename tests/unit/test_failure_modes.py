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


class TestHardWallClock:
    """🔴 실측 사고 — httpx 타임아웃이 못 막는 행(hang) (2026-08-19, 골든셋 v11 SV-022).

        11:52:42  요청 시작
        12:02:21  clova transport error: Server disconnected without sending a response.
        → 579초. `read=25s`를 걸어두고도 9분 39초를 매달렸다.

    `httpx.Timeout(read=25)`는 **읽기 연산 하나**의 상한이지 요청 전체의 상한이 아니다.
    연결이 열린 채 아무것도 오지 않다가 끊기는 패턴에서 발동하지 않는다.

    평가 타임아웃 300초 기준 이 사건 하나가 그 문항을 0점으로 만든다 —
    실제로 v11에서 정확도는 177/177인데 1건이 578초로 초과했다.
    """

    def test_hang_is_cut(self):
        """멈춘 호출을 상한에서 끊는다."""
        import time as _t

        from dart_agent.llm.hard_deadline import HardTimeout, run_bounded

        def hang():
            _t.sleep(30)
            return "너무 늦음"

        t0 = _t.monotonic()
        with pytest.raises(HardTimeout):
            run_bounded(hang, 0.3)
        assert _t.monotonic() - t0 < 2.0, "상한이 실제로 끊지 못했다"

    def test_fast_call_passes_through(self):
        from dart_agent.llm.hard_deadline import run_bounded
        assert run_bounded(lambda x: x * 2, 5.0, 21) == 42

    def test_zero_budget_refuses(self):
        from dart_agent.llm.hard_deadline import HardTimeout, run_bounded
        with pytest.raises(HardTimeout):
            run_bounded(lambda: 1, 0)

    def test_inner_exception_propagates(self):
        """실제 오류는 그대로 올라와야 한다 — 상한이 오류를 삼키면 안 된다."""
        from dart_agent.llm.hard_deadline import run_bounded
        with pytest.raises(ValueError):
            run_bounded(lambda: (_ for _ in ()).throw(ValueError("실제 오류")), 5.0)

    def test_narrate_degrades_on_hang(self):
        """행이 나도 **답변은 나간다** — 결정론 본문 그대로."""
        import time as _t

        from dart_agent.agent import narrate as N

        class Hang:
            name = "clova"
            def chat(self, *a, **k):
                _t.sleep(20)
                return None

        body = "삼성전자의 2024년 연결기준 매출액은 300,870,903백만원입니다. [C1]"
        orig = N._WALL_CLOCK_CAP_S
        N._WALL_CLOCK_CAP_S = 0.3
        try:
            t0 = _t.monotonic()
            text, why = N.narrate(Hang(), body, question="q")
            assert text == body, "행 발생 시 본문이 손상됐다"
            assert "벽시계" in why, why
            assert _t.monotonic() - t0 < 2.0, "호출자가 행에 붙잡혔다"
        finally:
            N._WALL_CLOCK_CAP_S = orig
