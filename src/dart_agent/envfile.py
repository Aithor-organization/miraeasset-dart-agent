"""`.env` 로더 — 의존성 없이 직접 읽는다.

🔴 **왜 python-dotenv를 안 쓰는가**

이 파일은 심사위원이 `python3 run_server.py` 한 줄로 재현할 때 실행된다.
`python-dotenv`는 requirements에 없었고, 없는 환경에서 import가 터지면
서버가 아예 안 뜬다. 15줄이면 되는 일에 그 위험을 지지 않는다.

🔴 **왜 이 파일이 생겼는가 (실측 사고, 2026-08-19)**

    $ python3 run_server.py
    WARNING CLOVA_API_KEY 미설정 → StubProvider 사용

`.env`에 키가 **있는데도** 아무도 읽지 않았다. 이전 실행들은 셸에서
`set -a; source .env`를 미리 했기 때문에 우연히 동작했을 뿐이고,
문서가 재현 명령으로 적어둔 `python3 run_server.py`만으로는 LLM이
붙지 않는다. 문서대로 따라 한 사람은 서술 계층 없는 시스템을 보게 된다.

**이미 있는 환경변수는 덮어쓰지 않는다** — 셸에서 명시로 준 값이
파일보다 강해야 한다. 배포 환경(NCP)에서 주입한 값을 `.env`가
가로채면 안 되기 때문이다.
"""

from __future__ import annotations

import os
from pathlib import Path


def load_env(path: str | Path = ".env") -> list[str]:
    """`.env`를 읽어 `os.environ`에 채운다. 채운 키 이름들을 돌려준다.

    파일이 없으면 빈 리스트 — 정상이다(환경변수로 직접 주입하는 배포 경로).
    """
    p = Path(path)
    if not p.is_file():
        return []

    loaded: list[str] = []
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        if key.startswith("export "):          # `export KEY=v` 표기 허용
            key = key[7:].strip()
        if not key or key in os.environ:        # 🔴 기존 값 우선
            continue
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]                      # 감싼 따옴표만 제거
        os.environ[key] = val
        loaded.append(key)
    return loaded
