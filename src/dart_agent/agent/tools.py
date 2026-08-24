"""Agent 도구 6종 — 전부 순수 함수 (SPEC §4 AC-T1~T4).

🔴 설계 D1: 모든 도구는 값과 함께 **출처**를 반환한다. 값만 주는 API는 금지 —
   LLM이 근거 없이 숫자를 쓰게 되는 경로를 원천 차단한다.
🔴 AC-T2: compute의 operand는 fact_id 참조다. 숫자 리터럴 입력 금지.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

from ..metrics import by_key
from ..models import CONSOLIDATED
from ..numbers import fmt_krw
from ..store.corrections import chain_of

# ── 반환 타입 ───────────────────────────────────────────────────────────────


import re as _re

# 표 행 라벨의 주석 참조 제거: "매출액 (주29)" → "매출액", "영업이익 (주21,22)" → "영업이익"
_NOTE_REF = _re.compile(r"\s*\(\s*주\s*[\d,\s·]*\s*\)\s*$")


@dataclass
class FactHit:
    fact_id: int
    corp_code: str
    corp_name: str
    metric_key: str
    label_ko: str
    fy: int
    basis: str
    period_scope: str | None
    value_krw: int | None
    raw_value: str
    raw_unit: str | None
    unit_confidence: str
    source: str
    doc_id: str
    report_nm: str
    rcept_no: str
    rcept_dt: str
    src_section: str | None

    @property
    def label_clean(self) -> str:
        """답변 문장용 라벨 — 주석 참조 제거. 인용에는 원문 label_ko를 쓴다."""
        return _NOTE_REF.sub("", self.label_ko).strip() or self.label_ko

    def citation_text(self) -> str:
        sec = f" · {self.src_section}" if self.src_section else ""
        return (
            f"{self.corp_name} {self.report_nm} · 접수번호 {self.rcept_no}{sec}\n"
            f"      {self.label_ko} {self.raw_value}"
            f"{' ' + self.raw_unit if self.raw_unit else ''} "
            f"({'연결' if self.basis == CONSOLIDATED else '별도'}"
            f"{', ' + self.period_scope if self.period_scope else ''}, {self.source})"
        )


@dataclass
class EventHit:
    doc_id: str
    corp_code: str
    corp_name: str
    domain: str  # contract|capital|holding
    event_kind: str
    amount_krw: int | None
    dt: str | None
    detail: str
    report_nm: str
    rcept_no: str
    extra: dict = field(default_factory=dict)

    def citation_text(self) -> str:
        amt = f" {fmt_krw(self.amount_krw)}" if self.amount_krw is not None else ""
        return (
            f"{self.corp_name} {self.report_nm} · 접수번호 {self.rcept_no}\n"
            f"      {self.event_kind}{amt}"
            f"{' · ' + self.dt if self.dt else ''}"
            f"{' · ' + self.detail[:60] if self.detail else ''}"
        )


@dataclass
class ComputeResult:
    op: str
    ok: bool
    value: float | None = None
    unit: str = ""
    detail: str = ""
    refused_reason: str | None = None
    operands: list[FactHit] = field(default_factory=list)


@dataclass
class ChainResult:
    mode: str
    nodes: list[dict] = field(default_factory=list)
    diffs: list[dict] = field(default_factory=list)
    linked: list[dict] = field(default_factory=list)


# ── 1. fact_query ──────────────────────────────────────────────────────────

_FACT_SQL = """
SELECT f.fact_id,f.corp_code,c.corp_name,f.metric_key,f.label_ko,f.fy,f.basis,
       f.period_scope,f.value_krw,f.raw_value,f.raw_unit,f.unit_confidence,f.source,
       f.doc_id,d.report_nm,d.rcept_no,d.rcept_dt,f.src_section,d.doc_subtype,d.base_year
FROM fin_fact f
JOIN document d ON d.doc_id=f.doc_id
JOIN company  c ON c.corp_code=f.corp_code
WHERE f.metric_key=? AND f.axis IS NULL AND d.is_effective=1
"""

# 본표 우선순위 — 주석(III-3-*)에 같은 값이 중복 등재되므로 본표를 채택한다.
def _section_rank(path: str | None) -> int:
    if not path:
        return 9
    if path.startswith("III-2") or path.startswith("III-4"):
        return 0  # 연결/별도 재무제표 본표
    if path.startswith("III-1"):
        return 1  # 요약재무정보
    if path.startswith("III-3") or path.startswith("III-5"):
        return 3  # 주석
    return 5


def fact_query(
    conn: sqlite3.Connection,
    *,
    corp: list[str],
    metric: str,
    fy: list[int],
    basis: str | None = None,
    prefer_annual: bool = True,
    scope: str | None = None,
) -> list[FactHit]:
    """재무 지표 결정론 조회. 정정 반영 유효본만 (AC-T3).

    중복 해소 규칙 (결정론):
      1) 본표(III-2/III-4) > 요약(III-1) > 주석(III-3)
      2) annual 보고서 > half/quarter (prefer_annual)
      3) period_scope: annual은 FY, 그 외는 누적(A) 우선 — 당기 3개월(Q)과 섞지 않는다
      4) 동일 조건이면 rcept_dt 최신

    🔴 `scope`를 주면 **그 기간 범위로 필터**한다 (FY/HYA/HYQ/QTA/QTQ).
       주지 않으면 위 순위로 하나를 고르며, 사실상 연간(FY)이 선택된다.

       이 파라미터가 없던 동안 "상반기 매출"을 물어도 **연간 값이 반환**됐다.
       period_scope가 정렬 순위로만 쓰이고 필터로는 쓰이지 않았기 때문이다
       (Gold Set scope_split 0/20으로 발견 — 2026-08-18).
       질의에 기간 표현이 있으면 호출자가 반드시 이 값을 넘겨야 한다.
    """
    mdef = by_key(metric)
    if mdef is None or not corp or not fy:
        return []
    sql = _FACT_SQL
    args: list = [metric]
    sql += f" AND f.corp_code IN ({','.join('?' * len(corp))})"
    args += list(corp)
    sql += f" AND f.fy IN ({','.join('?' * len(fy))})"
    args += list(fy)
    if basis:
        sql += " AND f.basis=?"
        args.append(basis)
    if scope:
        sql += " AND f.period_scope=?"
        args.append(scope)
    rows = conn.execute(sql, args).fetchall()

    def sort_key(r: sqlite3.Row):
        scope = r["period_scope"] or ""
        subtype = r["doc_subtype"] or ""
        annual_rank = 0 if subtype == "annual" else (1 if subtype == "half" else 2)
        if not prefer_annual:
            annual_rank = 0
        # 누적(A) 또는 FY 선호, 당기 3개월(Q 접미)은 후순위
        scope_rank = 0 if (scope == "FY" or scope.endswith("A")) else 1
        # 🔴 해당 회계연도의 보고서를 우선 인용한다.
        #    FY2024 값은 사업보고서(2024.12)의 당기 컬럼에서 뽑는 것이,
        #    사업보고서(2025.12)의 전기 컬럼에서 뽑는 것보다 근거로 자연스럽다.
        #    (값은 같지만 채점자가 원문 대조하기 쉬운 쪽을 택한다 — 평가지표 2)
        own_year_rank = 0 if (r["base_year"] == r["fy"]) else 1
        return (
            _section_rank(r["src_section"]),
            annual_rank,
            own_year_rank,
            scope_rank,
            0 if r["source"] == "xbrl" else 1,
            0 if r["unit_confidence"] == "high" else 1,
            -int(r["rcept_dt"] or 0),
        )

    best: dict[tuple, sqlite3.Row] = {}
    for r in rows:
        key = (r["corp_code"], r["fy"], r["basis"])
        cur = best.get(key)
        if cur is None or sort_key(r) < sort_key(cur):
            best[key] = r
    return [
        FactHit(
            fact_id=r["fact_id"], corp_code=r["corp_code"], corp_name=r["corp_name"],
            metric_key=r["metric_key"], label_ko=r["label_ko"], fy=r["fy"], basis=r["basis"],
            period_scope=r["period_scope"], value_krw=r["value_krw"], raw_value=r["raw_value"],
            raw_unit=r["raw_unit"], unit_confidence=r["unit_confidence"], source=r["source"],
            doc_id=r["doc_id"], report_nm=r["report_nm"], rcept_no=r["rcept_no"],
            rcept_dt=r["rcept_dt"], src_section=r["src_section"],
        )
        for r in sorted(best.values(), key=lambda x: (x["corp_name"], -x["fy"]))
    ]


# ── 2. get_section ─────────────────────────────────────────────────────────


def get_section(
    conn: sqlite3.Connection,
    *,
    corp: list[str],
    paths: list[str],
    year: int | None = None,
    doc_subtype: str | None = None,
    max_chars: int = 6000,
) -> list[dict]:
    """목차 주소로 섹션 원문 직접 조회 (D2). 검색보다 먼저 시도한다."""
    if not corp or not paths:
        return []
    sql = (
        "SELECT s.section_id,s.doc_id,s.corp_code,c.corp_name,s.path,s.title,s.text,"
        "       s.tables_md,s.content_class,d.report_nm,d.rcept_no,d.base_year,d.doc_subtype "
        "FROM section s JOIN document d ON d.doc_id=s.doc_id "
        "JOIN company c ON c.corp_code=s.corp_code "
        "WHERE d.is_effective=1 AND d.doc_group='periodic'"
    )
    args: list = []
    sql += f" AND s.corp_code IN ({','.join('?' * len(corp))})"
    args += list(corp)
    # path는 접두 매칭 (III-2 지정 시 III-2-2 포함)
    sql += " AND (" + " OR ".join("s.path = ? OR s.path LIKE ? || '-%'" for _ in paths) + ")"
    for p in paths:
        args += [p, p]
    if year:
        sql += " AND d.base_year=?"
        args.append(year)
    if doc_subtype:
        sql += " AND d.doc_subtype=?"
        args.append(doc_subtype)
    sql += " ORDER BY d.base_year DESC, d.base_month DESC, s.path LIMIT 40"

    out: list[dict] = []
    for r in conn.execute(sql, args):
        body = r["text"] or ""
        if r["content_class"] in ("financial_stmt", "table_registry") and r["tables_md"]:
            body = (r["tables_md"] or "")[:max_chars]
        out.append({
            "section_id": r["section_id"], "doc_id": r["doc_id"], "corp_name": r["corp_name"],
            "path": r["path"], "title": r["title"], "report_nm": r["report_nm"],
            "rcept_no": r["rcept_no"], "base_year": r["base_year"],
            "doc_subtype": r["doc_subtype"], "content_class": r["content_class"],
            "text": body[:max_chars],
        })
    return out


# ── 3. doc_search (BM25 단독 / vectors+embedder 주입 시 RRF 하이브리드) ──


def doc_search(index, query: str, *, corp: list[str] | None = None,
               doc_groups: list[str] | None = None, years: list[int] | None = None,
               top_k: int = 8, vectors=None, embedder=None, conn=None, rrf_k: int = 10,
               vec_weight: float = 2.0):
    """vectors(VectorStore)와 embedder가 함께 주입되면 BM25+벡터 RRF 융합.

    🔴 **점수 의미 보존**: abstention이 `max(hit.score)`를 BM25 스케일 임계값
    (기본 0.35)과 비교한다 — RRF 점수(≤0.033)를 그대로 돌려주면 전 질의가
    기권으로 쏠린다. 융합은 **순서**를 바꾸고, 점수는 다음 규칙으로 복원한다
    (Codex 리뷰 2026-08-24 major 2건 반영):
    - BM25 팔에 있던 문서 → BM25 점수 그대로
    - 벡터 단독 문서 → **코사인 유사도** (0~1). 상수 0.0을 주면 BM25가 못 찾고
      벡터만 정답을 찾은 경우 전부 기권으로 죽는다. 임계값 0.35는 BM25 기준
      보정값이라 스케일이 다르다는 한계는 있으나(관련 섹션 bge-m3 코사인은
      통상 0.5+), 벡터 팔의 발견을 기권 게이트에 전달할 유일한 통로다.
    - BM25 1위는 **항상 결과에 포함** — RRF 절단에서 탈락하면 max(score)가
      무너져 0.35 게이트가 정상 질의를 오기권한다.

    🔴 **가용성 우선**: 질의 임베딩 실패(레이트리밋/네트워크)는 BM25 단독으로
    조용히 강등한다. 검색이 죽는 것보다 융합을 포기하는 쪽이 옳다 (AC-R5).
    """
    if index is None or index.size == 0:
        return []
    corp_set = set(corp) if corp else None
    group_set = set(doc_groups) if doc_groups else None
    # 벡터 스토어는 periodic 문서만 담는다 — 다른 그룹 지정 질의에는 벡터 팔 제외
    hybrid = (vectors is not None and embedder is not None
              and (group_set is None or "periodic" in group_set))
    if not hybrid:
        return index.search(
            query, top_k=top_k, corp_codes=corp_set,
            doc_groups=group_set, years=set(years) if years else None,
        )

    from ..retrieval.bm25 import SearchHit, rrf_fuse
    from ..retrieval.vectors import fetch_doc

    bm25 = index.search(
        query, top_k=30, corp_codes=corp_set,
        doc_groups=group_set, years=set(years) if years else None,
    )
    try:
        qvec = embedder.embed([query])[0]
    except Exception:
        return bm25[:top_k]
    vhits = vectors.search(qvec, top_k=30, corp_codes=corp_set,
                           years=set(years) if years else None)
    if not vhits:
        return bm25[:top_k]

    # rrf_fuse는 first-seen Doc을 유지한다 — bm25를 앞에 두어 본문 있는 Doc 우선.
    # 가중치는 벡터 팔 우위 실측 반영 (rrf_fuse docstring의 MRR 표)
    fused = rrf_fuse([bm25, vhits], k=rrf_k, weights=[1.0, vec_weight])[:top_k]
    if bm25 and all(h.doc_key != bm25[0].doc_key for h in fused):
        fused[-1] = bm25[0]                    # BM25 1위 생존 보장 (위 docstring)
    bm_score = {h.doc_key: h.score for h in bm25}
    cos_score = {h.doc_key: h.score for h in vhits}
    out = []
    for h in fused:
        doc = h.doc
        if not doc.text and conn is not None:
            doc = fetch_doc(conn, h.doc_key)   # 벡터 단독 승자만 본문 보강
            if doc is None:                    # 비유효 문서로 강등된 섹션 → 제외
                continue
        out.append(SearchHit(
            h.doc_key,
            bm_score.get(h.doc_key) or cos_score.get(h.doc_key, 0.0),
            doc, h.reasons,
        ))
    return out


# ── 4. event_query ─────────────────────────────────────────────────────────

_EVENT_CFG = {
    "contract": ("contract_event", "event_kind", "amount_krw", "decision_dt"),
    "capital": ("capital_event", "event_kind", "amount_krw", "decision_dt"),
    "holding": ("holding_event", "change_reason", None, "report_dt"),
}


def event_query(
    conn: sqlite3.Connection,
    *,
    corp: list[str],
    domain: str,
    kinds: list[str] | None = None,
    year: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[EventHit]:
    """계약·자금조달·지분 이벤트 조회. 정정 반영 유효본만 (AC-T3)."""
    cfg = _EVENT_CFG.get(domain)
    if cfg is None or not corp:
        return []
    table, kind_col, amt_col, dt_col = cfg
    sql = (
        f"SELECT e.*, c.corp_name, d.report_nm, d.rcept_no, d.rcept_dt "
        f"FROM {table} e JOIN document d ON d.doc_id=e.doc_id "
        f"JOIN company c ON c.corp_code=e.corp_code "
        f"WHERE d.is_effective=1"
    )
    args: list = []
    sql += f" AND e.corp_code IN ({','.join('?' * len(corp))})"
    args += list(corp)
    if kinds:
        sql += " AND (" + " OR ".join(f"e.{kind_col} LIKE ?" for _ in kinds) + ")"
        args += [f"%{k}%" for k in kinds]
    lo = date_from or (f"{year}0101" if year else None)
    hi = date_to or (f"{year}1231" if year else None)
    if lo:
        sql += f" AND COALESCE(e.{dt_col}, d.rcept_dt) >= ?"
        args.append(lo)
    if hi:
        sql += f" AND COALESCE(e.{dt_col}, d.rcept_dt) <= ?"
        args.append(hi)
    sql += f" ORDER BY COALESCE(e.{dt_col}, d.rcept_dt) DESC LIMIT 200"

    out: list[EventHit] = []
    for r in conn.execute(sql, args):
        keys = r.keys()
        extra = {k: r[k] for k in keys if k not in
                 ("doc_id", "corp_code", "corp_name", "report_nm", "rcept_no", "rcept_dt")}
        detail = ""
        if domain == "contract":
            detail = " / ".join(
                str(x) for x in (r["contract_kind"], r["detail"], r["counterparty"]) if x
            )
        elif domain == "capital":
            try:
                detail = ", ".join(
                    f"{k}={v}" for k, v in list(json.loads(r["detail_json"]).items())[:4]
                )
            except Exception:
                detail = ""
        else:
            detail = (
                f"{r['reporter'] or ''} {r['rate_before']}%→{r['rate_after']}%"
            )
        out.append(EventHit(
            doc_id=r["doc_id"], corp_code=r["corp_code"], corp_name=r["corp_name"],
            domain=domain, event_kind=str(r[kind_col] or ""),
            amount_krw=(r[amt_col] if amt_col else None),
            dt=(r[dt_col] or r["rcept_dt"]), detail=detail,
            report_nm=r["report_nm"], rcept_no=r["rcept_no"], extra=extra,
        ))
    return out


# ── 5. trace_chain ─────────────────────────────────────────────────────────


def trace_chain(conn: sqlite3.Connection, *, mode: str, doc_id: str | None = None,
                corp_code: str | None = None, year: int | None = None) -> ChainResult:
    res = ChainResult(mode=mode)
    if mode == "correction" and doc_id:
        for r in chain_of(conn, doc_id):
            res.nodes.append({
                "doc_id": r["doc_id"], "report_nm": r["report_nm"], "rcept_dt": r["rcept_dt"],
                "is_correction": bool(r["is_correction"]),
                "is_effective": bool(r["is_effective"]),
            })
        for r in conn.execute(
            "SELECT item,before_val,after_val,reason,target_submit_dt FROM correction_diff "
            "WHERE doc_id=? LIMIT 30", (doc_id,)
        ):
            res.diffs.append(dict(r))
        return res

    if mode == "contract_lifecycle" and corp_code:
        # 체결 ↔ 해지 링크: 동일 기업의 해지 공시를 시간순으로 나열하고 체결과 대조
        sql = (
            "SELECT e.doc_id,e.event_kind,e.contract_kind,e.detail,e.counterparty,"
            "       e.amount_krw,e.start_dt,e.end_dt,e.decision_dt,d.report_nm,d.rcept_no "
            "FROM contract_event e JOIN document d ON d.doc_id=e.doc_id "
            "WHERE d.is_effective=1 AND e.corp_code=?"
        )
        args: list = [corp_code]
        if year:
            sql += " AND COALESCE(e.decision_dt,d.rcept_dt) BETWEEN ? AND ?"
            args += [f"{year}0101", f"{year}1231"]
        sql += " ORDER BY COALESCE(e.decision_dt,d.rcept_dt)"
        rows = [dict(r) for r in conn.execute(sql, args)]
        res.nodes = rows
        terminated = [r for r in rows if "해지" in (r["event_kind"] or "")]
        signed = [r for r in rows if "체결" in (r["event_kind"] or "")]
        for t in terminated:
            match = None
            for s in signed:
                # 상대·금액 근사 일치로 연결 (결정론 규칙)
                if s["counterparty"] and s["counterparty"] == t["counterparty"]:
                    match = s
                    break
                if s["amount_krw"] and s["amount_krw"] == t["amount_krw"]:
                    match = s
                    break
            res.linked.append({"terminated": t, "matched_signing": match})
        return res

    if mode == "holding_history" and corp_code:
        for r in conn.execute(
            "SELECT e.doc_id,e.reporter,e.rate_before,e.rate_after,e.change_reason,"
            "       e.report_dt,e.prev_report_dt,d.rcept_no FROM holding_event e "
            "JOIN document d ON d.doc_id=e.doc_id WHERE e.corp_code=? "
            "ORDER BY e.report_dt", (corp_code,)
        ):
            res.nodes.append(dict(r))
    return res


# ── 6. compute ─────────────────────────────────────────────────────────────


def compute(op: str, operands: list[FactHit]) -> ComputeResult:
    """증감률·비중·순위·합계. LLM은 산술을 직접 하지 않는다 (D1).

    AC-T4: unit_confidence='low' operand가 있으면 compare/rank를 거부한다
           (단위 오인 시 1,000배 오차 — R5b).
    """
    if not operands:
        return ComputeResult(op, False, refused_reason="operand 없음")
    if any(o.value_krw is None for o in operands):
        return ComputeResult(op, False, operands=operands,
                             refused_reason="일부 값의 금액을 공시에서 확정할 수 없습니다")
    if op in ("compare", "rank", "delta", "delta_pct", "share_pct") and any(
        o.unit_confidence != "high" for o in operands
    ):
        return ComputeResult(op, False, operands=operands,
                             refused_reason="일부 값의 금액 단위를 공시에서 확정할 수 없습니다")

    vals = [int(o.value_krw) for o in operands]

    if op == "sum":
        return ComputeResult(op, True, float(sum(vals)), "원",
                             f"합계 {fmt_krw(sum(vals))}", operands=operands)
    if op == "delta":
        if len(vals) != 2:
            return ComputeResult(op, False, operands=operands, refused_reason="operand 2개 필요")
        d = vals[0] - vals[1]
        return ComputeResult(op, True, float(d), "원",
                             f"차이 {fmt_krw(d)}", operands=operands)
    if op == "delta_pct":
        if len(vals) != 2:
            return ComputeResult(op, False, operands=operands, refused_reason="operand 2개 필요")
        base = vals[1]
        if base == 0:
            return ComputeResult(op, False, operands=operands, refused_reason="기준값 0 — 증감률 계산 불가")
        pct = (vals[0] - base) / abs(base) * 100
        return ComputeResult(op, True, pct, "%",
                             f"{'증가' if pct >= 0 else '감소'} {abs(pct):.1f}%", operands=operands)
    if op == "share_pct":
        if len(vals) < 2:
            return ComputeResult(op, False, operands=operands, refused_reason="operand 2개+ 필요")
        total = sum(vals[1:])
        if total == 0:
            return ComputeResult(op, False, operands=operands, refused_reason="분모 0")
        return ComputeResult(op, True, vals[0] / total * 100, "%",
                             f"비중 {vals[0] / total * 100:.1f}%", operands=operands)
    if op in ("compare", "rank"):
        order = sorted(operands, key=lambda o: -int(o.value_krw))
        detail = " > ".join(f"{o.corp_name} {fmt_krw(o.value_krw)}" for o in order)
        return ComputeResult(op, True, float(order[0].value_krw), "원", detail, operands=order)
    return ComputeResult(op, False, operands=operands, refused_reason=f"미지원 연산: {op}")
