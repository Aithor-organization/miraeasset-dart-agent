"""테스트 공통 픽스처. 실제 코퍼스를 쓴다 (AC-TEST1 — 합성 데이터 금지)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

CORPUS = ROOT / "docs" / "3.공시" / "corpus"
DB = ROOT / "index" / "dart.sqlite"


def pytest_configure(config):
    config.addinivalue_line("markers", "corpus: 실제 코퍼스 파일 필요")
    config.addinivalue_line("markers", "index: 빌드된 인덱스 DB 필요")


needs_corpus = pytest.mark.skipif(
    not (CORPUS / "manifest.jsonl").exists(), reason="코퍼스 없음"
)
needs_index = pytest.mark.skipif(not DB.exists(), reason="인덱스 미빌드")


@pytest.fixture(scope="session")
def corpus_root() -> Path:
    return CORPUS


@pytest.fixture(scope="session")
def manifest() -> list[dict]:
    path = CORPUS / "manifest.jsonl"
    if not path.exists():
        pytest.skip("코퍼스 없음")
    return [json.loads(line) for line in path.open(encoding="utf-8")]


@pytest.fixture(scope="session")
def conn():
    if not DB.exists():
        pytest.skip("인덱스 미빌드 — scripts/build_index.py 실행 필요")
    from dart_agent.store.db import connect

    c = connect(DB, read_only=True)
    yield c
    c.close()


@pytest.fixture(scope="session")
def orch(conn):
    from dart_agent.agent.orchestrator import Orchestrator
    from dart_agent.config import load_config
    from dart_agent.llm.provider import build_providers
    from dart_agent.retrieval.fts_index import FtsIndex, build_fts, fts_ready
    from dart_agent.retrieval.tokenizer import default_tokenizer

    cfg = load_config()
    llm, _emb, _notes = build_providers(cfg)
    # 🔴 서버(api/server.py)와 같은 색인 경로를 쓴다.
    #    인메모리 load_index()를 쓰면 전체 코퍼스(112,797섹션)에서 29분·수 GB가 든다 —
    #    운영 경로도 아닌 것을 테스트가 재현하는 셈이라 FTS5로 통일한다.
    tok = default_tokenizer()
    if not fts_ready(conn, expect_tokenizer=tok.mode):
        build_fts(conn, tokenizer=tok, limit=400)
    idx = FtsIndex(conn, tokenizer=tok)
    return Orchestrator(conn, cfg, index=idx, llm=llm)


def make_meta(row: dict):
    from dart_agent.models import DocMeta

    return DocMeta(
        doc_id=row["doc_id"], corp_code=str(row["corp_code"]), corp_name=row["corp_name"],
        doc_group=row["doc_group"], doc_subtype=row.get("doc_subtype"),
        report_nm=row["report_nm"], rcept_no=str(row["rcept_no"]),
        rcept_dt=str(row["rcept_dt"]), is_correction=bool(row.get("is_correction")),
        base_year=row.get("base_year"), base_month=row.get("base_month"),
        file_path=row["file_path"], file_format=row.get("file_format", "xml"),
    )
