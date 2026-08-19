"""🔴 하드 데드라인 — httpx 타임아웃이 못 막는 행(hang)을 스레드로 자른다.

**실측 사고 (2026-08-19, 골든셋 v11 SV-022)**

    11:52:42  요청 시작
    12:02:21  clova transport error: Server disconnected without sending a response.
    → 579초. `read=25s` 타임아웃을 걸어두고도 9분 39초를 매달려 있었다.

`httpx.Timeout(read=25)`는 **읽기 연산 하나**의 상한이지 요청 전체의 상한이 아니다.
연결이 열린 채 아무것도 오지 않다가 끊기는 패턴에서는 발동하지 않는다.

평가 타임아웃은 300초다. 이런 사건이 **한 번만 나도 그 문항은 0점**이고,
실제로 v11에서 1/177이 578초로 초과했다 — 정확도는 100%였는데 시간으로 잃는다.

Python은 같은 스레드에서 블로킹 소켓 read를 끊을 수 없다. 그래서 호출을 별도
스레드에 넣고 **호출자가 기다리기를 포기**한다. 버려진 스레드는 소켓이 끊길 때
스스로 죽는다 — daemon이라 프로세스 종료를 막지도 않는다.

🔴 대가를 분명히 한다: 스레드는 즉시 회수되지 않는다. 최악의 경우 몇 분간
   살아 있지만, **호출자는 그 시간을 쓰지 않는다.** 그게 요점이다.
"""

from __future__ import annotations

import concurrent.futures as _fut
import logging
from typing import Any, Callable, TypeVar

log = logging.getLogger(__name__)

T = TypeVar("T")

# daemon 스레드 풀 — 버려진 호출이 프로세스 종료를 막지 않는다.
_POOL = _fut.ThreadPoolExecutor(max_workers=8, thread_name_prefix="llm")


class HardTimeout(TimeoutError):
    """벽시계 상한 초과 — 호출자는 즉시 강등해야 한다."""


def run_bounded(fn: Callable[..., T], timeout_s: float, *args: Any, **kw: Any) -> T:
    """`fn`을 `timeout_s` 안에 끝내거나 `HardTimeout`을 던진다.

    타임아웃 시 작업을 **취소하지 않는다** — 이미 실행 중인 블로킹 호출은
    취소가 불가능하다. 대신 결과를 기다리지 않고 떠난다.
    """
    if timeout_s <= 0:
        raise HardTimeout(f"잔여 예산 없음 ({timeout_s:.1f}s)")
    future = _POOL.submit(fn, *args, **kw)
    try:
        return future.result(timeout=timeout_s)
    except _fut.TimeoutError as exc:
        log.warning("LLM 호출이 %.1fs를 넘겨 포기한다 (스레드는 백그라운드에서 소멸)",
                    timeout_s)
        raise HardTimeout(f"{timeout_s:.1f}s 초과 — 호출 포기") from exc
