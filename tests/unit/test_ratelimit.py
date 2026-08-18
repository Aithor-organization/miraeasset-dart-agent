"""CLOVA rate limit 대응 회귀 가드 (2026-08-18).

🔴 실사고: 골드셋 177문항 중 **60건이 429로 폐기**됐다. 호출률은 분당 12회로
   한도(60회/60,000토큰)의 1/5이었는데도 그랬다 — 429가 **즉시** 반환되는 바람에
   다음 호출이 0.14초 뒤에 또 두드렸기 때문이다. 실패만 페이싱을 잃는다.
"""

import time

import pytest

from dart_agent.llm.ratelimit import Pacer, parse_reset, retry_wait


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
        assert 13.0 < w <= 14.0

    def test_retry_after_takes_priority(self):
        w = retry_wait({"retry-after": "2s", "x-ratelimit-reset-tokens": "13s"}, 0)
        assert 2.0 < w <= 3.0

    def test_backoff_when_no_header(self):
        a, b = retry_wait({}, 0), retry_wait({}, 1)
        assert b > a, "헤더가 없으면 지수 백오프여야 한다"

    def test_capped(self):
        """평가 타임아웃 300초 안에 들어와야 한다."""
        assert retry_wait({"retry-after": "600s"}, 0) <= 20.0
        assert retry_wait({}, 10) <= 20.0


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
        assert calls["n"] == 4, "무한 재시도는 평가 타임아웃을 먹는다"

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
