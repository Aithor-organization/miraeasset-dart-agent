"""주식등의 대량보유상황보고서(5% 보고) 파서 (SPEC §1-1, AC-C2).

요약 수치는 전부 타입 셀로 표기된다 — holding 1,083건 전수 실측에서 아래 6개 ACODE가
문서당 정확히 1회, 누락 0건:  <TE ACODE="SUM_TMT_RT">11.85</TE> 등.

체인 포인터는 <TU AUNIT="BFR_RPT_DT" AUNITVALUE="20220422">로 문서에 명시돼 있어
하위 단계의 fuzzy 매칭이 불필요하다 (AC-C2). 단 최초 보고 49건은 AUNITVALUE="-"
이므로 YYYYMMDD 검증을 통과한 값만 채운다.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..models import HOLDING, CorrectionDiff, DocMeta, HoldingEvent, ParseResult
from ..numbers import clean_number

# TE/TU 타입 셀. 속성 순서가 문서마다 다르므로 속성은 개별 검색한다.
_CELL = re.compile(r"<T([EU])\b([^>]*)>(.*?)</T\1>", re.S)
_ACODE = re.compile(r'\bACODE="([^"]*)"')
_AUNIT = re.compile(r'\bAUNIT="([^"]*)"')
_AUNITVALUE = re.compile(r'\bAUNITVALUE="([^"]*)"')
_TAG = re.compile(r"<[^>]+>")
_YMD8 = re.compile(r"^\d{8}$")
_KO_DATE = re.compile(r"(\d{4})\s*[년./-]\s*(\d{1,2})\s*[월./-]\s*(\d{1,2})")
_TABLE = re.compile(r"<TABLE\b[^>]*>(.*?)</TABLE>", re.S)
_ROW = re.compile(r"<TR\b[^>]*>(.*?)</TR>", re.S)
_TH = re.compile(r"<TH\b[^>]*>(.*?)</TH>", re.S)
_TD = re.compile(r"<TD\b[^>]*>(.*?)</TD>", re.S)
_ANY_CELL = re.compile(r"<T[HDEU]\b[^>]*>(.*?)</T[HDEU]>", re.S)

_TARGET_KIND = re.compile(r"정정대상\s*공시서류\s*[:：]\s*(.{1,80}?)\s*(?:\d\s*\.|$)")
# 최초제출일은 '2024년 08월 02일' / '2023-04-03' / '2023.12.29.' / '2024. 6. 20.'로 혼용된다.
# 다음 항목 번호('3.')를 종결자로 쓰면 연도의 '2023.'에 먼저 걸리므로 날짜 토큰을 직접 집는다.
_TARGET_DT = re.compile(
    r"최초\s*제출일\s*[:：]?\s*(\d{8}|\d{4}\s*[년./-]\s*\d{1,2}\s*[월./-]\s*\d{1,2})"
)
_REASON = re.compile(r"정정\s*사항\s*[:：]?\s*(.{0,300})")


def _text(raw: str) -> str:
    """중첩 태그(<SPAN> 등) 제거 + 공백 정규화."""
    return re.sub(r"\s+", " ", _TAG.sub(" ", raw)).strip()


def _squash(raw: str) -> str:
    return re.sub(r"\s+", "", raw)


def _ymd(value: str | None) -> str | None:
    """YYYYMMDD 또는 '2024년 08월 02일' / '2023-04-03' → YYYYMMDD. 실패 시 None."""
    v = (value or "").strip()
    if _YMD8.match(v):
        return v
    m = _KO_DATE.search(v)
    return f"{m.group(1)}{int(m.group(2)):02d}{int(m.group(3)):02d}" if m else None


def _as_int(raw: str | None) -> int | None:
    v = clean_number(raw)
    return None if v is None else int(round(v))


def _scan_cells(xml: str) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """문서 1회 순회로 ACODE → 텍스트, AUNIT → AUNITVALUE 인덱스를 만든다."""
    by_acode: dict[str, list[str]] = {}
    by_aunit: dict[str, list[str]] = {}
    for m in _CELL.finditer(xml):
        attrs, body = m.group(2), m.group(3)
        code = _ACODE.search(attrs)
        if code:
            by_acode.setdefault(code.group(1), []).append(_text(body))
        unit = _AUNIT.search(attrs)
        if unit:
            val = _AUNITVALUE.search(attrs)
            by_aunit.setdefault(unit.group(1), []).append(val.group(1) if val else "")
    return by_acode, by_aunit


def _aunit_ymd(by_aunit: dict[str, list[str]], *keys: str) -> str | None:
    """AUNIT 날짜 셀을 우선순위대로 훑어 첫 유효 YYYYMMDD를 반환한다."""
    for key in keys:
        for raw in by_aunit.get(key) or []:
            got = _ymd(raw)
            if got:
                return got
    return None


def _labelled_reporter(xml: str) -> str | None:
    """'보고자' 라벨 셀 바로 뒤의 값 셀 (ACODE 부재 시 방어용 폴백)."""
    for row in _ROW.finditer(xml):
        cells = [_text(c) for c in _ANY_CELL.findall(row.group(1))]
        for i, cell in enumerate(cells[:-1]):
            sq = _squash(cell)
            if sq.startswith("보고자") and "관계" not in sq:
                nxt = next((c for c in cells[i + 1 :] if c), None)
                if nxt:
                    return nxt
    return None


def _find_diff_table(xml: str) -> tuple[int, list[str], str] | None:
    """정정 대조표(항목/정정사유/정정 전/정정 후)를 찾는다. CORRECTION 블록은
    본문 전체를 감싸므로 헤더로 해당 TABLE 하나만 골라야 한다."""
    for m in _TABLE.finditer(xml):
        heads = [_squash(_text(h)) for h in _TH.findall(m.group(1))]
        if any("정정전" in h for h in heads) and any("정정후" in h for h in heads):
            return m.start(), heads, m.group(1)
    return None


def _corrections(xml: str, doc_id: str) -> list[CorrectionDiff]:
    """정정공시의 원본 포인터 + 항목별 정정전/정정후. 대조표가 없어도 1행은 남긴다."""
    found = _find_diff_table(xml)
    start, heads, block = found if found else (len(xml), [], "")
    lead = _text(xml[:start])

    kind, dt, rsn = (r.search(lead) for r in (_TARGET_KIND, _TARGET_DT, _REASON))
    base = {
        "doc_id": doc_id,
        "target_doc_kind": (kind.group(1).strip() or None) if kind else None,
        "target_submit_dt": _ymd(dt.group(1)) if dt else None,
    }
    global_reason = (rsn.group(1).strip() or None) if rsn else None

    def col(*names: str) -> int | None:
        return next((i for i, h in enumerate(heads) if any(n in h for n in names)), None)

    # 열 수는 4열(항목/정정사유/정정전/정정후) 또는 5열(+정정요구여부)로 갈린다.
    idx = (col("항목"), col("정정사유"), col("정정전"), col("정정후"))
    rows: list[CorrectionDiff] = []
    for row in _ROW.finditer(block):
        tds = [_text(t) for t in _TD.findall(row.group(1))]
        if len(tds) < 2 or not any(tds):
            continue
        if len(tds) == len(heads) and None not in (idx[0], idx[2], idx[3]):
            item, before, after = tds[idx[0]], tds[idx[2]], tds[idx[3]]
            reason = tds[idx[1]] if idx[1] is not None else None
        else:  # rowspan/colspan으로 열 수가 어긋난 행 — 꼬리 기준 폴백
            item, before, after = tds[0], tds[-2], tds[-1]
            reason = tds[-3] if len(tds) >= 4 else None
        rows.append(
            CorrectionDiff(
                **base,
                reason=reason or global_reason,
                item=item or None,
                before_val=before or None,
                after_val=after or None,
            )
        )
    return rows or [CorrectionDiff(**base, reason=global_reason, item=None)]


class HoldingParser:
    """지분공시 요약행 1건 + (정정 시) CorrectionDiff를 산출한다."""

    doc_group = HOLDING

    def parse(self, meta: DocMeta, corpus_root: Path) -> ParseResult:
        result = ParseResult(meta=meta)
        path = Path(corpus_root) / meta.file_path / f"{meta.rcept_no}.xml"
        try:
            xml = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            result.warn(f"read failed: {path.name} ({exc})")
            return result
        try:  # 파서는 예외를 올리지 않고 부분 결과를 반환한다 (AC-P1)
            result.holding_events.append(self._event(meta, xml, result))
        except Exception as exc:
            result.warn(f"holding summary failed: {type(exc).__name__}: {exc}")
        if meta.is_correction:
            try:
                result.corrections.extend(_corrections(xml, meta.doc_id))
            except Exception as exc:
                result.warn(f"correction diff failed: {type(exc).__name__}: {exc}")
        return result

    def _event(self, meta: DocMeta, xml: str, result: ParseResult) -> HoldingEvent:
        by_acode, by_aunit = _scan_cells(xml)

        def first(code: str) -> str | None:
            vals = by_acode.get(code) or []
            if len(vals) > 1:  # 다중 보고자 문서 — 요약행은 첫 출현을 쓴다
                result.warn(f"multiple {code} ({len(vals)})")
            return vals[0] if vals else None

        rate_after = clean_number(first("SUM_TMT_RT"))
        if rate_after is None:
            result.warn("SUM_TMT_RT missing — 요약 보유비율 미확인")
        return HoldingEvent(
            doc_id=meta.doc_id,
            corp_code=meta.corp_code,
            reporter=first("RPT_RSP_NM") or first("RPT_RSP_NM1") or _labelled_reporter(xml),
            cnt_before=_as_int(first("SUM_BMT_CNT")),
            rate_before=clean_number(first("SUM_BMT_RT")),
            cnt_after=_as_int(first("SUM_TMT_CNT")),
            rate_after=rate_after,
            change_reason=first("SUM_CHN_RWN") or None,
            report_dt=_aunit_ymd(by_aunit, "THS_RPT_DT", "RPT_RSP_DT") or meta.rcept_dt,
            prev_report_dt=_aunit_ymd(by_aunit, "BFR_RPT_DT"),
        )
