"""평가용 API 서버 (SPEC §7).

주최측 계약: GET /answer?question_id=&question=
  → {question_id, question, retrieved_context, think_trace, answer}

🔴 AC-API2: HTTP 500을 반환하지 않는다. 모든 예외를 abstention 응답 + 200으로 변환한다.
   평가 기간(09.07~09.20) 중 한 문항의 예외가 그 문항 0점을 넘어 서버 장애로 번지면 안 된다.
"""

from __future__ import annotations

import logging
import time

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse

from . import ratelimit_mw
from .. import counters
from ..config import load_config
from ..llm.provider import build_providers
from ..retrieval.bm25 import load_saved_index  # 레거시 pickle 캐시 폴백용
from ..store.db import connect, get_meta, table_counts
from ..agent.orchestrator import Orchestrator

log = logging.getLogger("dart_agent")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

app = FastAPI(title="공시 Agent (Disclosure Analyst)", version="0.1.0")

_STATE: dict = {"ready": False, "notes": [], "started_at": None}


@app.on_event("startup")
def _startup() -> None:
    t0 = time.time()
    cfg = load_config()
    _STATE["cfg"] = cfg
    _STATE["started_at"] = int(t0)
    try:
        conn = connect(cfg.db_path, read_only=True)
    except FileNotFoundError as exc:
        _STATE["notes"].append(f"인덱스 미구축: {exc} — scripts/build_index.py 실행 필요")
        log.error("index missing: %s", exc)
        return
    _STATE["conn"] = conn

    llm, emb, notes = build_providers(cfg)
    _STATE["notes"].extend(notes)
    for n in notes:
        log.warning(n)
    _STATE["llm"], _STATE["emb"] = llm, emb

    from ..retrieval.fts_index import FtsIndex, fts_ready
    from ..retrieval.tokenizer import default_tokenizer
    tok_mode = default_tokenizer().mode

    # ① FTS5 디스크 색인 우선 (운영 경로).
    #    실측: 112,797섹션에서 기동 2.4s · 서버 상주 273 MB. 인메모리 BM25였다면
    #    상주 3.4 GB / 로드 피크 9.7 GB로 NCP 권장 4 GB 서버에서 기동 중 죽는다.
    if fts_ready(conn, expect_tokenizer=tok_mode):
        idx = FtsIndex(conn, tokenizer=default_tokenizer())
        log.info("FTS5 색인 사용: %s (%d 섹션)", cfg.db_path, idx.size)
    else:
        # ② 구 pickle 캐시 (레거시 — 메모리 상주. 소규모 색인에서만 안전)
        idx = load_saved_index(cfg.bm25_path, expect_tokenizer=tok_mode)
        if idx is not None:
            log.info("BM25 캐시 로드: %s (%d 섹션)", cfg.bm25_path, idx.size)
            _STATE["notes"].append(
                "레거시 인메모리 BM25 사용 중 — 전체 코퍼스에서 메모리 초과 위험. "
                "scripts/build_index.py로 FTS5 색인 생성 권장"
            )
        else:
            # ③ 색인이 아예 없다. 🔴 여기서 인메모리 BM25를 만들면 안 된다 —
            #    전체 코퍼스에서 29분이 걸리고 4 GB 서버는 그 전에 OOM으로 죽는다.
            #    같은 비용이면 **올바른 산출물**(FTS5)을 만든다. 메모리는 일정하다.
            n_sec = conn.execute("SELECT count(*) FROM section").fetchone()[0]
            _STATE["notes"].append(
                f"색인 없음 → 기동 중 FTS5 빌드 시작 ({n_sec:,} 섹션, 수 분~30분 소요). "
                "배포 전 scripts/build_index.py를 실행해 두면 이 지연이 없습니다."
            )
            log.warning(_STATE["notes"][-1])
            try:
                from ..retrieval.fts_index import build_fts
                conn_rw = connect(cfg.db_path)  # 빌드는 쓰기 연결이 필요하다
                build_fts(conn_rw, tokenizer=default_tokenizer())
                conn_rw.close()
                idx = FtsIndex(conn, tokenizer=default_tokenizer())
                log.info("FTS5 색인 생성 완료: %d 섹션", idx.size)
            except Exception as exc:
                # 읽기 전용 파일시스템 등 — 검색 없이도 사실 조회는 동작해야 한다
                _STATE["notes"].append(f"FTS5 빌드 실패({exc}) → 검색 계층 비활성")
                log.error(_STATE["notes"][-1])
                idx = None
    _STATE["index"] = idx
    if tok_mode != "kiwi":
        _STATE["notes"].append("kiwipiepy 미설치 → 문자 n-gram BM25로 강등 (검색 품질 저하)")
        log.warning(_STATE["notes"][-1])

    _STATE["orch"] = Orchestrator(conn, cfg, index=idx, llm=llm)
    _STATE["ready"] = True
    log.info(
        "ready in %.1fs · sections=%d · tokenizer=%s · llm=%s",
        time.time() - t0, idx.size, tok_mode, getattr(llm, "name", "?"),
    )


@app.middleware("http")
async def _rate_limit(request: Request, call_next):
    """🔴 유입 제한은 **미들웨어**로 건다.

    엔드포인트 시그니처에 `Request`를 넣으면 함수를 직접 호출하는 테스트가
    깨진다 (실측). 관심사도 분리되지 않는다 — 제한은 횡단 관심사다.
    """
    if request.url.path == "/answer":
        limited = ratelimit_mw.check(request)
        if limited is not None:
            return limited
    return await call_next(request)


def _counters() -> dict:
    """런타임 카운터 — `notes`(기동 스냅샷)가 못 보는 강등을 여기서 본다."""
    return counters.snapshot()


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/ready")
def ready() -> dict:
    return {
        "ready": bool(_STATE.get("ready")),
        "sections_indexed": getattr(_STATE.get("index"), "size", 0),
        # 🔴 `notes`는 **기동 시 스냅샷**이다 — 런타임 강등은 여기 안 나온다.
        #    (AITHOR `lifecycle-operator` 지적: RUNBOOK이 지시하는 감시 지표가
        #     실제로는 그 사건을 관측하지 못했다) → 런타임 카운터를 함께 낸다.
        "notes": _STATE.get("notes", []),
        "runtime": _counters(),
        "rate_limit": ratelimit_mw.stats(),
    }


@app.get("/meta")
def meta() -> dict:
    conn = _STATE.get("conn")
    if conn is None:
        return {"error": "index not loaded"}
    return {
        "build": get_meta(conn),
        "tables": table_counts(conn),
        "llm": getattr(_STATE.get("llm"), "name", None),
        "embedding": getattr(_STATE.get("emb"), "name", None),
        "notes": _STATE.get("notes", []),
    }


@app.get("/answer")
def answer(
    question_id: str = Query(..., max_length=200, description="평가 문항 식별자"),
    # 🔴 길이 상한 — 없으면 초장문 질의로 HCX 토큰 예산을 태울 수 있다.
    #    실제 공시 질문은 100자를 넘지 않는다 (골든셋 최장 62자).
    question: str = Query(..., max_length=500, description="질의 원문"),
) -> JSONResponse:
    """계약 4필드를 항상 포함해 200으로 응답한다 (AC-API1, AC-API2)."""
    if not question or not question.strip():
        return JSONResponse(
            status_code=400,
            content={
                "question_id": question_id, "question": question or "",
                "retrieved_context": "", "think_trace": "[오류] question 파라미터가 비어 있습니다.",
                "answer": "질의가 비어 있어 답변할 수 없습니다.",
                "abstained": True, "abstain_reason": "empty_question",
            },
        )

    orch: Orchestrator | None = _STATE.get("orch")
    if orch is None:
        return JSONResponse(
            status_code=200,
            content={
                "question_id": question_id, "question": question,
                "retrieved_context": "(인덱스 미로드)",
                "think_trace": "[오류] 인덱스가 로드되지 않았습니다. "
                               + "; ".join(_STATE.get("notes", [])),
                "answer": "현재 공시 인덱스가 준비되지 않아 답변할 수 없습니다.",
                "abstained": True, "abstain_reason": "index_not_ready",
            },
        )

    try:
        ans = orch.answer(question_id, question)
        return JSONResponse(status_code=200, content=ans.to_payload())
    except Exception as exc:  # 최후 방어 — 500 금지
        log.exception("answer failed: %s", exc)
        return JSONResponse(
            status_code=200,
            content={
                "question_id": question_id, "question": question,
                "retrieved_context": "(처리 중 오류)",
                "think_trace": f"[오류] {type(exc).__name__}",
                "answer": "요청을 처리하는 중 오류가 발생해 답변을 생성하지 못했습니다.",
                "abstained": True, "abstain_reason": "internal_error",
            },
        )
