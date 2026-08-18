"""SQLite 연결 · 스키마 초기화 (SPEC §2-1)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

_SCHEMA = Path(__file__).with_name("schema.sql")


def connect(db_path: Path, *, read_only: bool = False) -> sqlite3.Connection:
    """연결 반환. read_only=True면 URI 모드로 쓰기 차단 (서버 런타임용)."""
    db_path = Path(db_path)
    if read_only:
        if not db_path.exists():
            raise FileNotFoundError(f"index not built: {db_path}")
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, check_same_thread=False)
    else:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """스키마 적용. CREATE IF NOT EXISTS라 재실행 안전 (AC-S4)."""
    conn.executescript(_SCHEMA.read_text(encoding="utf-8"))
    conn.commit()


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO build_meta(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )


def get_meta(conn: sqlite3.Connection) -> dict[str, str]:
    return {r["key"]: r["value"] for r in conn.execute("SELECT key,value FROM build_meta")}


def table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    """/meta 및 빌드 리포트용 행 수 집계."""
    tables = (
        "company",
        "company_alias",
        "document",
        "fin_fact",
        "section",
        "contract_event",
        "capital_event",
        "holding_event",
        "correction_diff",
        "registry_row",
    )
    out: dict[str, int] = {}
    for t in tables:
        try:
            out[t] = conn.execute(f"SELECT COUNT(*) AS c FROM {t}").fetchone()["c"]
        except sqlite3.Error:
            out[t] = -1
    return out
