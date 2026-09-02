#!/usr/bin/env python3
"""서버 진입점 — `python3 run_server.py` (SPEC §7-2 재현성).

sys.path에 src/를 넣으므로 PYTHONPATH 설정이 불필요하다.
uvicorn을 직접 쓰려면: PYTHONPATH=src python3 -m uvicorn dart_agent.api.server:app
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT / "src"))


def main() -> int:
    import uvicorn

    from dart_agent.envfile import load_env

    # 🔴 uvicorn 기동 **전에** 채운다 — 설정은 앱 임포트 시점에 읽힌다.
    #    이게 없으면 `.env`에 키가 있어도 StubProvider로 뜬다 (2026-08-19 실측).
    loaded = load_env(_ROOT / ".env")
    if loaded:
        print(f"[env] .env에서 {len(loaded)}개 로드: {', '.join(sorted(loaded))}")

    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    workers = int(os.environ.get("WORKERS", "1"))
    # 인덱스를 워커별로 메모리 로드하므로 workers>1은 메모리 배수 주의
    uvicorn.run(
        "dart_agent.api.server:app",
        host=host, port=port, workers=workers, log_level=os.environ.get("LOG_LEVEL", "info"),
        # 🔴 질의는 GET 쿼리로 온다 — 한글은 URL 인코딩에서 1자당 9바이트가 되므로
        #    긴 질문이 h11의 요청 라인 상한에 먼저 걸린다.
        #    실측(2026-09-02, 배포 서버): 한글 2,000자(URL 18KB) 200 → 3,000자(27KB) **400**.
        #    400은 5xx가 아니라 주최측 재시도를 유발하지 않으므로 그 문항을 그냥 잃는다.
        #    64KB면 한글 약 7,000자까지 받는다 — 평가 질의가 그보다 길 이유는 없다.
        h11_max_incomplete_event_size=64 * 1024,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
