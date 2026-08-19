"""`.env` 로더 회귀 가드.

🔴 이 테스트가 지키는 사고: `.env`에 CLOVA 키가 있는데도 서버가
StubProvider로 떠서 골든셋 전체가 LLM 없이 돌았다 (2026-08-19).
로더가 조용히 빈손으로 돌아오면 같은 일이 재발한다.
"""

from __future__ import annotations

import os

from dart_agent.envfile import load_env


def test_기본_로드(tmp_path):
    p = tmp_path / ".env"
    p.write_text("FOO_TEST_A=1\nFOO_TEST_B=hello\n", encoding="utf-8")
    try:
        loaded = load_env(p)
        assert set(loaded) == {"FOO_TEST_A", "FOO_TEST_B"}
        assert os.environ["FOO_TEST_B"] == "hello"
    finally:
        for k in ("FOO_TEST_A", "FOO_TEST_B"):
            os.environ.pop(k, None)


def test_기존_환경변수를_덮지_않는다(tmp_path):
    """셸에서 명시로 준 값이 파일보다 강해야 한다 — 배포 환경 주입 보호."""
    os.environ["FOO_TEST_KEEP"] = "shell"
    p = tmp_path / ".env"
    p.write_text("FOO_TEST_KEEP=file\n", encoding="utf-8")
    try:
        loaded = load_env(p)
        assert "FOO_TEST_KEEP" not in loaded
        assert os.environ["FOO_TEST_KEEP"] == "shell"
    finally:
        os.environ.pop("FOO_TEST_KEEP", None)


def test_주석과_빈줄과_따옴표(tmp_path):
    p = tmp_path / ".env"
    p.write_text(
        "# 주석\n\n"
        'FOO_TEST_Q="quoted value"\n'
        "export FOO_TEST_E=exported\n"
        "잘못된줄\n",
        encoding="utf-8",
    )
    try:
        loaded = load_env(p)
        assert os.environ["FOO_TEST_Q"] == "quoted value"   # 감싼 따옴표만 제거
        assert os.environ["FOO_TEST_E"] == "exported"       # export 접두 허용
        assert len(loaded) == 2                              # `=` 없는 줄은 무시
    finally:
        for k in ("FOO_TEST_Q", "FOO_TEST_E"):
            os.environ.pop(k, None)


def test_파일_없으면_빈_리스트(tmp_path):
    """환경변수 직접 주입 배포 경로 — 파일 부재는 오류가 아니다."""
    assert load_env(tmp_path / "없는파일") == []


def test_값에_등호가_있어도_보존(tmp_path):
    """API 키/토큰에 `=`가 흔하다 (base64 패딩). 첫 `=`에서만 자른다."""
    p = tmp_path / ".env"
    p.write_text("FOO_TEST_EQ=abc=def==\n", encoding="utf-8")
    try:
        load_env(p)
        assert os.environ["FOO_TEST_EQ"] == "abc=def=="
    finally:
        os.environ.pop("FOO_TEST_EQ", None)
