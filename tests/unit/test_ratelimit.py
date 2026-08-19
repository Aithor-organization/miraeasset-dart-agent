"""CLOVA rate limit 대응 회귀 가드 (2026-08-18).

🔴 실사고: 골드셋 177문항 중 **60건이 429로 폐기**됐다. 호출률은 분당 12회로
   한도(60회/60,000토큰)의 1/5이었는데도 그랬다 — 429가 **즉시** 반환되는 바람에
   다음 호출이 0.14초 뒤에 또 두드렸기 때문이다. 실패만 페이싱을 잃는다.
"""

import time

import pytest

from dart_agent.llm.ratelimit import (MAX_RETRIES, Pacer, parse_reset,
                                      retry_wait)


class TestParseReset:
    @pytest.mark.parametrize("raw,want", [
        ("13s", 13.0),
        ("1m30s", 90.0),
        ("2m", 120.0),
        ("0.5s", 0.5),
        ("30", 30.0),          # 숫자만 오는 변형
    ])
    def test_parses(self, raw, want):
        assert parse_reset(raw) == want

    @pytest.mark.parametrize("raw", [None, "", "곧", "abc"])
    def test_unparseable_is_none(self, raw):
        assert parse_reset(raw) is None


class TestRetryWait:
    def test_prefers_header_over_backoff(self):
        """🔴 서버가 리셋 시각을 말해주면 그걸 따른다 — 추측보다 정확하다."""
        w = retry_wait({"x-ratelimit-reset-tokens": "13s"}, attempt=0)
        # 지터(base의 최대 30%, 상한 5초)가 얹히므로 상한이 넓다
        assert 13.0 < w <= 13.5 + 5.0

    def test_retry_after_takes_priority(self):
        w = retry_wait({"retry-after": "2s", "x-ratelimit-reset-tokens": "13s"}, 0)
        assert 2.0 < w <= 2.5 + 2.5 * 0.3

    def test_backoff_when_no_header(self):
        a, b = retry_wait({}, 0), retry_wait({}, 1)
        assert b > a, "헤더가 없으면 지수 백오프여야 한다"

    def test_capped(self):
        """평가 타임아웃 300초 안에 들어와야 한다 (지터 상한 +5초 포함)."""
        assert retry_wait({"retry-after": "600s"}, 0) <= 25.0
        assert retry_wait({}, 10) <= 25.0


class _Headers(dict):
    """httpx 헤더처럼 대소문자 무관 get."""

    def get(self, k, default=None):
        return super().get(k.lower(), default)


class TestPacer:
    def test_no_wait_when_plenty_left(self):
        p = Pacer()
        p.observe(_Headers({"x-ratelimit-remaining-tokens": "55000",
                            "x-ratelimit-remaining-requests": "58"}))
        t = time.monotonic()
        p.wait()
        assert time.monotonic() - t < 0.05, "여유가 있으면 자면 안 된다"

    def test_waits_when_tokens_nearly_gone(self):
        """🔴 429를 맞고 대응하는 것보다 **닿기 전에** 쉬는 편이 처리량이 높다.

        429는 그 호출을 버리지만 선제 대기는 버리지 않는다.
        """
        p = Pacer()
        p.observe(_Headers({"x-ratelimit-remaining-tokens": "500",
                            "x-ratelimit-remaining-requests": "40",
                            "x-ratelimit-reset-tokens": "0.2s"}))
        t = time.monotonic()
        p.wait()
        assert time.monotonic() - t >= 0.2

    def test_waits_when_requests_nearly_gone(self):
        p = Pacer()
        p.observe(_Headers({"x-ratelimit-remaining-tokens": "50000",
                            "x-ratelimit-remaining-requests": "1",
                            "x-ratelimit-reset-requests": "0.2s"}))
        t = time.monotonic()
        p.wait()
        assert time.monotonic() - t >= 0.2

    def test_malformed_headers_ignored(self):
        """헤더가 깨져도 죽지 않는다 — LLM은 보조 계층이라 멈추면 안 된다."""
        p = Pacer()
        p.observe(_Headers({"x-ratelimit-remaining-tokens": "?"}))
        t = time.monotonic()
        p.wait()
        assert time.monotonic() - t < 0.05

    def test_wait_is_consumed_once(self):
        p = Pacer()
        p.observe(_Headers({"x-ratelimit-remaining-tokens": "0",
                            "x-ratelimit-reset-tokens": "0.15s"}))
        p.wait()
        t = time.monotonic()
        p.wait()
        assert time.monotonic() - t < 0.05, "한 번 쉰 대기가 반복되면 안 된다"


class TestProviderRetry:
    """실제 provider가 429에서 재시도하는지 — 여기가 폐기 60건을 막는 지점이다."""

    def _provider(self, monkeypatch, statuses):
        import httpx

        from dart_agent.llm import clova

        calls = {"n": 0}

        class FakeClient:
            def __init__(self, **kw): pass
            def __enter__(self): return self
            def __exit__(self, *a): return False

            def post(self, url, **kw):
                i = calls["n"]; calls["n"] += 1
                st = statuses[min(i, len(statuses) - 1)]
                body = ({"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                         "usage": {}} if st == 200 else {"error": {"message": "rate"}})
                return httpx.Response(st, json=body,
                                      headers={"x-ratelimit-reset-tokens": "0.05s"},
                                      request=httpx.Request("POST", "http://x"))

        monkeypatch.setattr(clova.httpx, "Client", FakeClient)
        monkeypatch.setattr(clova.time, "sleep", lambda s: None)
        return clova.ClovaProvider("k", "http://x", "HCX-007"), calls

    def test_retries_429_then_succeeds(self, monkeypatch):
        p, calls = self._provider(monkeypatch, [429, 429, 200])
        assert p.chat([{"role": "user", "content": "q"}]).content == "ok"
        assert calls["n"] == 3, "429를 재시도하지 않았다"

    def test_gives_up_after_max_retries(self, monkeypatch):
        from dart_agent.llm.clova import ClovaError
        p, calls = self._provider(monkeypatch, [429])
        with pytest.raises(ClovaError):
            p.chat([{"role": "user", "content": "q"}])
        # MAX_RETRIES=2 → 최초 1 + 재시도 2 = 3회.
        # 🔴 3에서 2로 줄인 것은 의도된 변경이다 — 재시도 1회를 줄여도 정확도
        #    손실이 0이고(LLM 차단 시에도 177/177), 최악 지연은 65초 줄어든다.
        assert calls["n"] == MAX_RETRIES + 1, "재시도 횟수가 상한을 벗어났다"

    def test_4xx_not_retried(self, monkeypatch):
        """400/401은 재시도해도 같은 답 — 시간만 버린다."""
        from dart_agent.llm.clova import ClovaError
        p, calls = self._provider(monkeypatch, [401])
        with pytest.raises(ClovaError):
            p.chat([{"role": "user", "content": "q"}])
        assert calls["n"] == 1

    def test_5xx_retried(self, monkeypatch):
        p, calls = self._provider(monkeypatch, [503, 200])
        assert p.chat([{"role": "user", "content": "q"}]).content == "ok"
        assert calls["n"] == 2


class TestDeadline:
    """🔴 AITHOR `resilience-audit`가 찾아낸 치명 결함의 회귀 가드 (2026-08-19).

    지적: SPEC AC-API4의 `REQUEST_TIMEOUT_S`가 **코드 어디서도 읽히지 않았다**.
    그 결과 재시도·페이싱이 누적돼 최악 지연이 질의당 680초 — 평가 타임아웃
    300초의 227%. 429가 나는 순간 **정확도가 아니라 타임아웃으로 0점**이었다.

    상수 근거(수정 전): 20(Pacer) + 4×65(시도) + 3×20(재시도 sleep) = 340초/호출,
    질의당 LLM 2회 → 680초.
    """

    def test_remaining_infinite_without_deadline(self):
        from dart_agent.llm.ratelimit import remaining
        assert remaining(None) == float("inf")

    def test_exhausted_budget_skips_call(self, monkeypatch):
        """예산이 없으면 **호출을 시작하지 않는다** — 시작하면 순손실이다."""
        import time

        import httpx

        from dart_agent.llm import clova
        from dart_agent.llm.ratelimit import DeadlineExceeded

        calls = {"n": 0}

        class FakeClient:
            def __init__(self, **kw): pass
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def post(self, url, **kw):
                calls["n"] += 1
                return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}],
                                                 "usage": {}},
                                      request=httpx.Request("POST", "http://x"))

        monkeypatch.setattr(clova.httpx, "Client", FakeClient)
        p = clova.ClovaProvider("k", "http://x", "HCX-007")
        with pytest.raises(DeadlineExceeded):
            p.chat([{"role": "user", "content": "q"}], deadline=time.monotonic() - 1)
        assert calls["n"] == 0, "예산이 끝났는데 호출했다"

    def test_read_timeout_capped_by_budget(self):
        """고정 25초를 남은 예산보다 크게 쓰면 예산을 넘긴다."""
        import time

        from dart_agent.llm.clova import ClovaProvider
        t = ClovaProvider._timeout(time.monotonic() + 8)
        assert t.read <= 8, f"read={t.read} — 잔여 8초를 넘는다"

    def test_read_timeout_unbounded_without_deadline(self):
        from dart_agent.llm.clova import ClovaProvider, _READ_TIMEOUT_S
        assert ClovaProvider._timeout(None).read == _READ_TIMEOUT_S

    def test_pacer_refuses_to_sleep_past_budget(self):
        """예산을 넘겨 자면 그 요청은 어차피 타임아웃 — 자는 시간이 순손실이다."""
        import time

        from dart_agent.llm.ratelimit import DeadlineExceeded, Pacer
        p = Pacer()
        p.observe(_Headers({"x-ratelimit-remaining-tokens": "0",
                            "x-ratelimit-reset-tokens": "10s"}))
        with pytest.raises(DeadlineExceeded):
            p.wait(deadline=time.monotonic() + 1)

    def test_jitter_breaks_synchronization(self):
        """🔴 지터가 없으면 동시 요청이 **같은 순간** 깨어난다 (thundering herd).

        헤더 리셋값은 모두에게 동일하므로, 고정 오프셋만 더하면 확률이 아니라
        설계상 동기화다.
        """
        from dart_agent.llm.ratelimit import retry_wait
        h = _Headers({"x-ratelimit-reset-tokens": "10s"})
        waits = {retry_wait(h, 0) for _ in range(20)}
        assert len(waits) > 1, "재시도 대기가 결정론적 — 지터가 없다"

    def test_jitter_stays_bounded(self):
        from dart_agent.llm.ratelimit import retry_wait
        h = _Headers({"x-ratelimit-reset-tokens": "10s"})
        assert all(10.0 <= retry_wait(h, 0) <= 21.0 for _ in range(50))

    def test_worst_case_within_evaluation_budget(self):
        """🔴 상수만으로 최악 지연을 계산해 예산 안인지 확인한다.

        수정 전: 20 + 4×65 + 3×20 = 340초/호출 × 2 = 680초 (예산 227%)
        """
        from dart_agent.llm.clova import _READ_TIMEOUT_S
        from dart_agent.llm.ratelimit import MAX_RETRIES, _MAX_WAIT

        attempts = MAX_RETRIES + 1
        per_call = 5 + attempts * _READ_TIMEOUT_S + MAX_RETRIES * (_MAX_WAIT * 1.3)
        assert per_call < 300, f"호출당 최악 {per_call:.0f}초 — 예산 초과"
        # 🔴 그럼에도 2회 호출이면 넘는다 → 그래서 **데드라인 배선이 필수**다.
        #    이 단언은 "상수만으로는 부족하다"는 사실 자체를 고정한다.
        assert per_call * 2 > 200, "상수 축소만으로 안전하다고 오해하지 말 것"
