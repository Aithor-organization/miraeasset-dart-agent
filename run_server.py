#!/usr/bin/env python3
"""서버 진입점 — `python3 run_server.py` (SPEC §7-2 재현성).

sys.path에 src/를 넣으므로 PYTHONPATH 설정이 불필요하다.
uvicorn을 직접 쓰려면: PYTHONPATH=src python3 -m uvicorn dart_agent.api.server:app
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))


def main() -> int:
    import uvicorn

    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    workers = int(os.environ.get("WORKERS", "1"))
    # 인덱스를 워커별로 메모리 로드하므로 workers>1은 메모리 배수 주의
    uvicorn.run(
        "dart_agent.api.server:app",
        host=host, port=port, workers=workers, log_level=os.environ.get("LOG_LEVEL", "info"),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
