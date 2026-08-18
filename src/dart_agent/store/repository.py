"""ParseResult → SQLite 적재 (SPEC §2-1 AC-S1~S3).

단일 트랜잭션 · idempotent. 파서는 DB를 모르고, 여기서만 DB를 만진다.
"""

from __future__ import annotations

import sqlite3

from ..models import ParseResult


def upsert_document(conn: sqlite3.Connection, res: ParseResult) -> None:
    m = res.meta
    conn.execute(
        "INSERT INTO document(doc_id,corp_code,doc_group,doc_subtype,report_nm,rcept_no,"
        "rcept_dt,flr_nm,is_correction,base_year,base_month,file_path,file_format,parse_warnings) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(doc_id) DO UPDATE SET parse_warnings=excluded.parse_warnings",
        (
            m.doc_id,
            m.corp_code,
            m.doc_group,
            m.doc_subtype,
            m.report_nm,
            m.rcept_no,
            m.rcept_dt,
            None,
            1 if m.is_correction else 0,
            m.base_year,
            m.base_month,
            m.file_path,
            m.file_format,
            len(res.warnings),
        ),
    )


def store(conn: sqlite3.Connection, res: ParseResult) -> dict[str, int]:
    """ParseResult 전체를 적재. document가 먼저 들어가야 FK가 성립한다 (AC-S1)."""
    upsert_document(conn, res)
    n = {
        "fin_fact": 0,
        "section": 0,
        "contract_event": 0,
        "capital_event": 0,
        "holding_event": 0,
        "correction_diff": 0,
        "registry_row": 0,
    }

    for f in res.fin_facts:
        cur = conn.execute(
            "INSERT INTO fin_fact(doc_id,corp_code,acode,label_ko,metric_key,fy,period_kind,"
            "period_scope,basis,axis,value_krw,raw_value,raw_unit,unit_confidence,source,"
            "src_section) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(doc_id,acode,label_ko,fy,period_kind,period_scope,basis,axis) "
            "DO NOTHING",
            (
                f.doc_id, f.corp_code, f.acode, f.label_ko, f.metric_key, f.fy, f.period_kind,
                f.period_scope, f.basis, f.axis, f.value_krw, f.raw_value, f.raw_unit,
                f.unit_confidence, f.source, f.src_section,
            ),
        )
        n["fin_fact"] += cur.rowcount if cur.rowcount > 0 else 0

    for s in res.sections:
        cur = conn.execute(
            "INSERT INTO section(section_id,doc_id,corp_code,path,title,level,text,tables_md,"
            "char_len,content_class) VALUES(?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(section_id) DO NOTHING",
            (
                s.section_id, s.doc_id, s.corp_code, s.path, s.title, s.level, s.text,
                s.tables_md, s.char_len, s.content_class,
            ),
        )
        n["section"] += cur.rowcount if cur.rowcount > 0 else 0

    for e in res.contract_events:
        cur = conn.execute(
            "INSERT INTO contract_event(doc_id,corp_code,event_kind,contract_kind,detail,"
            "counterparty,amount_krw,recent_revenue_krw,ratio_pct,start_dt,end_dt,decision_dt) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(doc_id) DO NOTHING",
            (
                e.doc_id, e.corp_code, e.event_kind, e.contract_kind, e.detail, e.counterparty,
                e.amount_krw, e.recent_revenue_krw, e.ratio_pct, e.start_dt, e.end_dt,
                e.decision_dt,
            ),
        )
        n["contract_event"] += cur.rowcount if cur.rowcount > 0 else 0

    for e in res.capital_events:
        cur = conn.execute(
            "INSERT INTO capital_event(doc_id,corp_code,event_kind,amount_krw,decision_dt,"
            "detail_json) VALUES(?,?,?,?,?,?) ON CONFLICT(doc_id) DO NOTHING",
            (e.doc_id, e.corp_code, e.event_kind, e.amount_krw, e.decision_dt, e.detail_json),
        )
        n["capital_event"] += cur.rowcount if cur.rowcount > 0 else 0

    for e in res.holding_events:
        cur = conn.execute(
            "INSERT INTO holding_event(doc_id,corp_code,reporter,cnt_before,rate_before,"
            "cnt_after,rate_after,change_reason,report_dt,prev_report_dt) "
            "VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(doc_id) DO NOTHING",
            (
                e.doc_id, e.corp_code, e.reporter, e.cnt_before, e.rate_before, e.cnt_after,
                e.rate_after, e.change_reason, e.report_dt, e.prev_report_dt,
            ),
        )
        n["holding_event"] += cur.rowcount if cur.rowcount > 0 else 0

    for c in res.corrections:
        conn.execute(
            "INSERT INTO correction_diff(doc_id,target_doc_kind,target_submit_dt,reason,item,"
            "before_val,after_val) VALUES(?,?,?,?,?,?,?)",
            (
                c.doc_id, c.target_doc_kind, c.target_submit_dt, c.reason, c.item,
                c.before_val, c.after_val,
            ),
        )
        n["correction_diff"] += 1

    for r in res.registry_rows:
        conn.execute(
            "INSERT INTO registry_row(doc_id,registry_kind,row_json,src_section) VALUES(?,?,?,?)",
            (r.doc_id, r.registry_kind, r.row_json, r.src_section),
        )
        n["registry_row"] += 1

    return n


def clear_doc(conn: sqlite3.Connection, doc_id: str) -> None:
    """재적재 전 기존 레코드 제거 (idempotent 보장, AC-S4)."""
    for t in (
        "fin_fact", "section", "contract_event", "capital_event",
        "holding_event", "correction_diff", "registry_row",
    ):
        conn.execute(f"DELETE FROM {t} WHERE doc_id=?", (doc_id,))
