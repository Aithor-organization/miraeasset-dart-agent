"""런타임 카운터 — 강등을 **관측 가능하게** 만든다.

🔴 왜 필요한가 (AITHOR `lifecycle-operator` 지적):

    RUNBOOK.md는 *"`/ready`의 `notes`가 비어있는지가 핵심 지표"* 라고 지시했다.
    그런데 `notes`는 **기동 시 스냅샷**이다 — 평가 중 발생한 429·절단 강등은
    거기 영원히 나타나지 않는다. **런북이 지시하는 감시 지표가 그 사건을
    관측하지 못했다.**

    대안 경로는 서버 로그 grep뿐인데, 로그는 stdout이고 Dockerfile에 로그
    드라이버 지정이 없어 grep 대상 파일이 보장되지 않는다.

의존성 0 · 프로세스 로컬 · 스레드 안전. 프로세스가 죽으면 사라지는 것은
의도된 단순함이다 — 평가 기간 한정 단일 서버라 영속 저장이 필요 없다.
"""

from __future__ import annotations

import threading
from collections import Counter

_lock = threading.Lock()
_c: Counter[str] = Counter()


def bump(name: str, n: int = 1) -> None:
    with _lock:
        _c[name] += n


def snapshot() -> dict[str, int]:
    """현재 값. `/ready`가 그대로 노출한다."""
    with _lock:
        return dict(_c)


def reset() -> None:
    """테스트 전용 — 서빙 경로에서 호출하지 말 것."""
    with _lock:
        _c.clear()
