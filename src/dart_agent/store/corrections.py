"""정정 체인 해소 (SPEC §2-3 AC-C1~C4).

실측 근거: 거래소공시 정정 비율 43% (631/1,469). 미처리 시 원본+정정본 중복 집계로
「2025년 계약 총액」류 질의에서 체계적 과대계상이 발생한다 (proposal §2-2).

정정공시는 원본을 가리키는 포인터를 자체 보유한다:
  exchange/major/periodic → (정정관련 공시서류, 정정관련 공시서류제출일)  ← 근사 매칭 필요
  holding                 → BFR_RPT_DT (직전 보고일)                    ← 명시 포인터, 근사 불필요
"""

from __future__ import annotations

import difflib
import sqlite3
from dataclasses import dataclass, field


@dataclass
class ChainReport:
    resolved: int = 0
    unresolved: int = 0
    holding_linked: int = 0
    superseded: int = 0
    unresolved_docs: list[str] = field(default_factory=list)

    @property
    def total_corrections(self) -> int:
        return self.resolved + self.unresolved

    @property
    def match_rate(self) -> float:
        t = self.total_corrections
        return (self.resolved / t) if t else 0.0

    def summary(self) -> str:
        return (
            f"정정 체인: {self.resolved}/{self.total_corrections} 매칭 "
            f"({self.match_rate * 100:.1f}%) · holding 포인터 {self.holding_linked}건 · "
            f"superseded {self.superseded}건 · 미해소 {self.unresolved}건"
        )


def _shift(yyyymmdd: str, days: int) -> str:
    """날짜 ±N일. datetime 대신 순수 계산 (결정론)."""
    from datetime import date, timedelta

    try:
        d = date(int(yyyymmdd[:4]), int(yyyymmdd[4:6]), int(yyyymmdd[6:8]))
    except (ValueError, IndexError):
        return yyyymmdd
    return (d + timedelta(days=days)).strftime("%Y%m%d")


def resolve_chains(conn: sqlite3.Connection, *, date_window: int = 3) -> ChainReport:
    """정정 → 원본 링크를 설정하고 체인 종단에 is_effective=1을 남긴다."""
    rep = ChainReport()

    # 1) holding — 명시 포인터 (AC-C2)
    for row in conn.execute(
        "SELECT h.doc_id, h.corp_code, h.prev_report_dt FROM holding_event h "
        "WHERE h.prev_report_dt IS NOT NULL AND h.prev_report_dt <> ''"
    ).fetchall():
        prev = conn.execute(
            "SELECT d.doc_id FROM holding_event he JOIN document d ON d.doc_id=he.doc_id "
            "WHERE he.corp_code=? AND he.report_dt=? AND he.doc_id<>? LIMIT 1",
            (row["corp_code"], row["prev_report_dt"], row["doc_id"]),
        ).fetchone()
        if prev:
            conn.execute(
                "UPDATE document SET supersedes_doc_id=? WHERE doc_id=?",
                (prev["doc_id"], row["doc_id"]),
            )
            rep.holding_linked += 1

    # 2) exchange/major/periodic — (문서유형, 제출일) 근사 매칭 (AC-C1)
    corrections = conn.execute(
        "SELECT DISTINCT c.doc_id, c.target_doc_kind, c.target_submit_dt, "
        "       d.corp_code, d.doc_group, d.doc_subtype, d.report_nm "
        "FROM correction_diff c JOIN document d ON d.doc_id=c.doc_id "
        "WHERE d.doc_group <> 'holding'"
    ).fetchall()

    for c in corrections:
        target = _find_original(conn, c, date_window)
        if target:
            conn.execute(
                "UPDATE document SET supersedes_doc_id=? WHERE doc_id=?", (target, c["doc_id"])
            )
            rep.resolved += 1
        else:
            rep.unresolved += 1
            if len(rep.unresolved_docs) < 50:
                rep.unresolved_docs.append(c["doc_id"])

    # 3) 체인 종단 판정 — 다른 문서가 supersede하는 문서는 유효본이 아니다 (AC-C3)
    conn.execute("UPDATE document SET is_effective=1")
    cur = conn.execute(
        "UPDATE document SET is_effective=0 WHERE doc_id IN "
        "(SELECT supersedes_doc_id FROM document WHERE supersedes_doc_id IS NOT NULL)"
    )
    rep.superseded = cur.rowcount if cur.rowcount > 0 else 0
    conn.commit()
    return rep


def _find_original(conn: sqlite3.Connection, c: sqlite3.Row, window: int) -> str | None:
    """정정공시 1건의 원본 doc_id 탐색. 실패 시 None (보수적 — is_effective 유지)."""
    submit_dt = (c["target_submit_dt"] or "").strip()
    if not submit_dt or len(submit_dt) != 8:
        return None

    # 제출일 정확 일치 우선 → ±1일 → ±2일 … (가까운 날짜가 더 신뢰도 높음)
    offsets = [0]
    for step in range(1, window + 1):
        offsets.extend((step, -step))
    dates = [_shift(submit_dt, off) if off else submit_dt for off in offsets]
    seen: set[str] = set()
    for dt in dates:
        if dt in seen:
            continue
        seen.add(dt)
        cands = conn.execute(
            "SELECT doc_id, doc_subtype, report_nm FROM document "
            "WHERE corp_code=? AND doc_group=? AND rcept_dt=? AND is_correction=0 AND doc_id<>?",
            (c["corp_code"], c["doc_group"], dt, c["doc_id"]),
        ).fetchall()
        if not cands:
            continue
        if len(cands) == 1:
            return cands[0]["doc_id"]
        # 후보 다수 → 문서유형 문자열 유사도 최대 (AC-C1 2)
        want = (c["target_doc_kind"] or c["doc_subtype"] or c["report_nm"] or "").strip()
        best, best_score = None, -1.0
        for cand in cands:
            hay = f"{cand['doc_subtype'] or ''} {cand['report_nm'] or ''}"
            score = difflib.SequenceMatcher(None, want, hay).ratio()
            if score > best_score:
                best, best_score = cand["doc_id"], score
        return best
    return None


def effective_doc_ids(conn: sqlite3.Connection, corp_code: str, doc_group: str) -> list[str]:
    return [
        r["doc_id"]
        for r in conn.execute(
            "SELECT doc_id FROM document WHERE corp_code=? AND doc_group=? AND is_effective=1",
            (corp_code, doc_group),
        )
    ]


def chain_of(conn: sqlite3.Connection, doc_id: str) -> list[sqlite3.Row]:
    """doc_id가 속한 정정 체인 전체를 오래된 것부터 반환 (trace_chain 도구용)."""
    # 조상 방향
    node = doc_id
    back: list[str] = []
    for _ in range(20):
        row = conn.execute(
            "SELECT supersedes_doc_id FROM document WHERE doc_id=?", (node,)
        ).fetchone()
        if not row or not row["supersedes_doc_id"]:
            break
        node = row["supersedes_doc_id"]
        if node in back:
            break
        back.append(node)
    # 후손 방향
    node, fwd = doc_id, []
    for _ in range(20):
        row = conn.execute(
            "SELECT doc_id FROM document WHERE supersedes_doc_id=? LIMIT 1", (node,)
        ).fetchone()
        if not row:
            break
        node = row["doc_id"]
        if node in fwd:
            break
        fwd.append(node)

    order = list(reversed(back)) + [doc_id] + fwd
    ph = ",".join("?" * len(order))
    rows = {
        r["doc_id"]: r
        for r in conn.execute(
            f"SELECT doc_id,report_nm,rcept_dt,is_correction,is_effective,supersedes_doc_id "
            f"FROM document WHERE doc_id IN ({ph})",
            order,
        )
    }
    return [rows[d] for d in order if d in rows]
