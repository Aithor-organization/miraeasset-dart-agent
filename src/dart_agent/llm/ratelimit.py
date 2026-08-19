"""CLOVA rate limit 대응 — 재시도 + 선제 페이싱.

🔴 **실측 한도 (2026-08-18, 응답 헤더 직접 확인)**

    x-ratelimit-limit-requests : 60      (분당 요청)
    x-ratelimit-limit-tokens   : 60000   (분당 토큰)
    x-ratelimit-reset-*        : 13s     (리셋까지 남은 시간)

🔴 **왜 재시도가 없으면 실패가 실패를 부르는가**

골드셋 177문항 중 **60건이 429로 폐기**됐다. 그런데 이 실행은 순차 호출이라
분당 12회 남짓이었다 — 한도의 1/5이다. 로그를 보면 원인이 드러난다:

    13:54:18.707  429
    13:54:18.845  429   ← 0.14초 뒤
    13:54:19.048  429   ← 0.20초 뒤

**실패가 빠르게 반환되기 때문에** 다음 질문이 즉시 다시 호출한다. 한 번 한도에
닿으면 그 뒤로는 쉬는 구간 없이 계속 두드리게 되고, 리셋될 틈이 없다.
정상 응답은 5초씩 걸려 자연 페이싱이 되는데 **실패만 페이싱을 잃는다.**

그래서 이 모듈은 두 가지를 한다:
  1. 429/5xx에서 **헤더가 알려준 시간만큼 자고 재시도** (실패의 페이싱 복원)
  2. 남은 토큰이 바닥나기 **전에** 선제적으로 쉼 (한도에 닿는 것 자체를 회피)
"""

from __future__ import annotations

import logging
import random
import re
import time

log = logging.getLogger(__name__)

MAX_RETRIES = 2


class DeadlineExceeded(RuntimeError):
    """남은 예산으로는 이 호출을 끝낼 수 없다 — 호출자는 즉시 강등해야 한다."""


def remaining(deadline: float | None) -> float:
    """데드라인까지 남은 초. `None`이면 무한(∞)."""
    return float("inf") if deadline is None else deadline - time.monotonic()
_FALLBACK_WAIT = 5.0      # 헤더가 없을 때 (실측 리셋 창이 13초라 그 절반)
_MAX_WAIT = 20.0          # 평가 타임아웃 300초 대비 안전 상한
_DURATION = re.compile(r"(?:(\d+)m)?(?:(\d+(?:\.\d+)?)s)?")

# 선제 페이싱 임계 — 이 아래로 떨어지면 리셋을 기다린다.
# 서술 1회가 입력+추론+출력 합쳐 대략 1~2천 토큰이므로 여유를 둔다.
_TOKEN_FLOOR = 3000
_REQUEST_FLOOR = 3


def parse_reset(value: str | None) -> float | None:
    """`"13s"` / `"1m30s"` → 초. 파싱 실패는 None (호출자가 기본값 사용)."""
    if not value:
        return None
    m = _DURATION.fullmatch(value.strip())
    if not m or not any(m.groups()):
        try:
            return float(value)           # 숫자만 오는 변형 대비
        except ValueError:
            return None
    minutes = float(m.group(1) or 0)
    seconds = float(m.group(2) or 0)
    return minutes * 60 + seconds


def retry_wait(headers, attempt: int) -> float:
    """429를 받았을 때 잘 시간.

    헤더가 리셋 시각을 알려주면 **그것을 따른다** — 지수 백오프는 서버가
    말해주지 않을 때의 차선책이지, 알려줄 때까지 무시할 이유가 없다.
    """
    hinted = parse_reset(
        headers.get("retry-after")
        or headers.get("x-ratelimit-reset-tokens")
        or headers.get("x-ratelimit-reset-requests")
    )
    base = (min(hinted + 0.5, _MAX_WAIT) if hinted is not None
            else min(_FALLBACK_WAIT * (2 ** attempt), _MAX_WAIT))
    return base + _jitter(base)


def _jitter(base: float) -> float:
    """🔴 지터가 없으면 동시 요청이 **결정론적으로 동기화**된다.

    헤더가 알려주는 리셋 시각은 모든 요청에게 동일하다. 거기에 고정 `+0.5`만
    더하면 N개 요청이 **같은 순간 깨어나** 한꺼번에 다시 두드린다 — 확률적
    충돌이 아니라 설계상 동기화다(thundering herd). 난수 폭을 얹어 흩는다.
    """
    return random.uniform(0.0, min(base * 0.3, 5.0))


class Pacer:
    """직전 응답 헤더를 기억했다가 **다음 호출 전에** 필요하면 재운다.

    429를 맞고 나서 대응하는 것보다, 남은 토큰이 바닥나기 전에 쉬는 편이
    총 처리량이 높다 — 429는 그 호출을 버리지만 선제 대기는 버리지 않는다.
    """

    def __init__(self) -> None:
        self._sleep_until = 0.0

    def observe(self, headers) -> None:
        try:
            tokens = int(headers.get("x-ratelimit-remaining-tokens", "999999"))
            requests = int(headers.get("x-ratelimit-remaining-requests", "999"))
        except ValueError:
            return
        if tokens > _TOKEN_FLOOR and requests > _REQUEST_FLOOR:
            return
        reset = parse_reset(
            headers.get("x-ratelimit-reset-tokens")
            or headers.get("x-ratelimit-reset-requests")
        )
        wait = min((reset or _FALLBACK_WAIT) + 0.5, _MAX_WAIT)
        wait += _jitter(wait)          # 선제 대기도 동기화 대상이다
        self._sleep_until = time.monotonic() + wait
        log.info("rate limit 임박(토큰 %s·요청 %s) — %.1fs 선제 대기", tokens, requests, wait)

    def wait(self, deadline: float | None = None) -> None:
        """필요한 만큼 잔다. **남은 예산을 넘겨 자지는 않는다.**

        예산을 넘겨 자면 그 요청은 어차피 타임아웃이므로, 자는 시간이 순손실이다.
        """
        left = self._sleep_until - time.monotonic()
        if left <= 0:
            self._sleep_until = 0.0
            return
        budget = remaining(deadline)
        if left >= budget:
            self._sleep_until = 0.0
            raise DeadlineExceeded(f"페이싱 {left:.1f}s > 잔여 {budget:.1f}s")
        time.sleep(left)
        self._sleep_until = 0.0
