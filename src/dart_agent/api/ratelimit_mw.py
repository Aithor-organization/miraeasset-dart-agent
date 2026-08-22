"""유입 제한 — HCX 쿼터 고갈 방어 (AITHOR `security-engineer` 상위 #3).

🔴 `llm/ratelimit.py`의 `Pacer`는 **자기 페이싱**이지 유입 제한이 아니다.
   우리가 HCX를 부르는 속도는 조절하지만, **누가 우리를 얼마나 부르는지**는
   전혀 막지 않는다. 둘은 다른 층이다.

공격 시나리오 (지적 원문):

    제3자가 question 문자열만 바꿔 /answer를 반복 호출
      → 캐시 키가 (question_id, question) 쌍이라 무력화 (orchestrator.py)
      → 요청당 HCX 최대 2회 (목차 라우팅 + 서술)
    결과: 평가 기간 중 쿼터 소진 → 이후 전 문항이 템플릿으로 강등

인증을 넣지 않는 이유: 주최측 평가 엔드포인트라 **누가 부를지 모른다**.
읽기 전용 + 공개 데이터이므로 인증 부재 자체는 타당하다 — 문제는 인증이 아니라
**유입량**이다. 그래서 인증 대신 IP당 속도만 제한한다.

의존성 0 — dict + 시간창. 단일 프로세스 전제이며 워커가 늘면 워커별로 적용된다
(그 경우 실효 한도가 워커 수만큼 곱해진다는 점을 알고 쓰는 것이다).
"""

from __future__ import annotations

import os
import threading
import time
from collections import defaultdict, deque

from fastapi import Request
from fastapi.responses import JSONResponse

# 평가는 주최측이 순차 또는 소규모 병렬로 보낸다고 보고 넉넉히 잡는다.
# 실측 처리량이 분당 7~18건이므로 60은 정상 평가를 막지 않는다.
_LIMIT = int(os.environ.get("RATE_LIMIT_PER_MIN", "60"))
_WINDOW = 60.0

_hits: dict[str, deque[float]] = defaultdict(deque)
_lock = threading.Lock()


def _client_key(request: Request) -> str:
    """프록시 뒤를 고려한 클라이언트 식별자.

    🔴 `X-Forwarded-For`는 위조 가능하다 — 신뢰 경계 밖 헤더다. 그래도 쓰는 이유는
       NCP LB/nginx 뒤에서는 `client.host`가 전부 프록시 IP로 뭉개져 **제한이
       전역 하나가 되기 때문**이다. 위조 시 제한을 우회당하지만, 위조하지 않는
       정상 트래픽은 올바르게 분리된다. 둘 다 나쁘면 덜 나쁜 쪽을 고른다.
    """
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def check(request: Request) -> JSONResponse | None:
    """한도 초과면 429 응답, 아니면 None."""
    if _LIMIT <= 0:                      # 0이면 비활성 (로컬 부하시험용)
        return None
    key = _client_key(request)
    now = time.monotonic()
    with _lock:
        q = _hits[key]
        while q and now - q[0] > _WINDOW:
            q.popleft()
        if len(q) >= _LIMIT:
            retry = max(1, int(_WINDOW - (now - q[0])) + 1)
            return JSONResponse(
                status_code=429,
                headers={"Retry-After": str(retry)},
                # 🔴 429여도 계약 5필드를 유지한다 — 평가측 파서가 깨지지 않게.
                content={
                    "question_id": request.query_params.get("question_id", ""),
                    "question": request.query_params.get("question", ""),
                    "retrieved_context": "",
                    "think_trace": f"[유입 제한] IP당 분당 {_LIMIT}회 초과 — {retry}초 후 재시도",
                    "answer": "요청이 일시적으로 많습니다. 잠시 후 다시 시도해 주세요.",
                    "citations": [], "confidence": "low",
                    "abstained": True, "abstain_reason": "rate_limited",
                },
            )
        q.append(now)
    return None


def stats() -> dict:
    """`/ready` 노출용 — 현재 창에서 관측 중인 클라이언트 수."""
    with _lock:
        return {"limit_per_min": _LIMIT, "tracked_clients": len(_hits)}
