"""정기공시(사업/반기/분기보고서) 파서.

Stage A 목차 트리 → Section / Stage B XBRL TE(ACODE+ACONTEXT) → FinFact /
Stage C 표 폴백 — III-1 요약재무정보 (XBRL 미태깅 문서용, 실측 half·quarter 28%).

예외를 던지지 않는다 (AC-P1). DB/네트워크/시간/난수 미사용 (AC-P2, AC-P3).
"""

from __future__ import annotations

import bisect
import re
from pathlib import Path

from ..metrics import from_acode, from_label
from ..models import (CONSOLIDATED, DURATION, FINANCIAL_STMT, INSTANT, PERIODIC, PROSE,
                      SCOPE_FY, SCOPE_HYA, SCOPE_HYQ, SCOPE_QTA, SCOPE_QTQ, SEPARATE,
                      TABLE_REGISTRY, DocMeta, FinFact, ParseResult, Section)
from ..numbers import clean_number, detect_unit, to_krw

_TITLE = re.compile(r"<TITLE\b[^>]*>(.*?)</TITLE>", re.S)
_TE = re.compile(r'<TE\b([^>]*\bACODE="[^"]*"[^>]*)>(.*?)</TE>', re.S)
_TABLE = re.compile(r"<TABLE\b[^>]*>(.*?)</TABLE>", re.S)
_ROW = re.compile(r"<TR\b[^>]*>(.*?)</TR>", re.S)
_CELL = re.compile(r"<T[DEHU]\b[^>]*>(.*?)</T[DEHU]>", re.S)
_PARA = re.compile(r"<P\b[^>]*>(.*?)</P>", re.S)
# numbers._UNIT_DECL과 같은 표기 — 매치 텍스트를 detect_unit에 넘겨 정규화한다
_DECL = re.compile(r"단\s*위\s*[:：]?\s*[(（]?\s*[가-힣]{0,3}\s*원")
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
_ATTRS = {n: re.compile(n + r'="([^"]*)"') for n in ("ACODE", "ACONTEXT")}

_ROMAN = re.compile(r"^([IVXLC]+)\s*\.")
_DOTTED = re.compile(r"^(\d+)\s*-\s*(\d+)\s*\.")
_NUMBERED = re.compile(r"^(\d+)\s*\.")
_SUBHEAD = re.compile(r"^[가-하]\s*\.")
_FIN_T = re.compile(r"재무상태표|손익계산서|포괄손익|자본변동표|현금흐름표|요약재무정보")
_REG_T = re.compile(r"임원|직원|계열회사|종속회사|타법인출자|연구개발실적|주주")
_SUMMARY = re.compile(r"요약재무정보|요약연결재무정보")
# 🔴 실측 (반기·분기보고서 24건 표본): 기간 접미는 FY만이 아니다.
#    CFY2025dHYA(누적 6개월) · CFY2025dHYQ(당기 3개월) · PFY2024eHY · CFY2024dFY …
#    분기는 FQ(1분기) / TQ(3분기) 계열: dFQA·dFQQ·eFQA·eFQ / dTQA·dTQQ·eTQA·eTQ
#    → 접미를 (FY|HY|FQ|TQ|QT|Q\d) + 선택적 누적/당기 한정자(A|Q)로 받는다.
_CTX = re.compile(
    r"^(BPFY|PFY|CFY)(\d{4})([ed])(FY|HY|FQ|TQ|QT|Q\d)([AQ])?(?:_(.*))?$"
)
_YEAR = re.compile(r"(\d{4})\s*년")
# 보고서 종류 → 자기 기간 계열. duration fact는 이 계열 1개만 채택한다 (_pick_scope).
_FAMILY = {"annual": ("FY",), "half": ("HY",), "quarter": ("TQ", "FQ", "QT")}


def _text(frag: str) -> str:
    return _WS.sub(" ", _TAG.sub(" ", frag)).strip()


def _cells(row_html: str) -> list[str]:
    return [_text(c.group(1)) for c in _CELL.finditer(row_html)]


def _tables_md(chunk: str) -> str:
    """구간 내 표를 단순 파이프 행으로 렌더 (best-effort)."""
    out: list[str] = []
    for t in _TABLE.finditer(chunk):
        for r in _ROW.finditer(t.group(1)):
            cs = _cells(r.group(1))
            if any(cs):
                out.append("| " + " | ".join(cs) + " |")
        out.append("")
    return "\n".join(out).strip()


def _klass(title: str) -> str:
    if _FIN_T.search(title):
        return FINANCIAL_STMT
    return TABLE_REGISTRY if _REG_T.search(title) else PROSE


def _decl_index(xml: str) -> tuple[list[str | None], list[int]]:
    """문서 전체 단위 선언을 (단위, 오프셋) 정렬 인덱스로 1회만 선계산한다."""
    units: list[str | None] = []
    pos: list[int] = []
    for m in _DECL.finditer(xml):
        units.append(detect_unit(m.group(0)))
        pos.append(m.start())
    return units, pos


def _unit_at(units: list[str | None], pos: list[int], offset: int) -> str | None:
    """offset 직전 가장 가까운 단위 선언. 미검출 시 None → 값 생성 안 함 (AC-U1)."""
    i = bisect.bisect_left(pos, offset) - 1
    while i >= 0:
        if units[i]:
            return units[i]
        i -= 1
    return None


def _parse_context(
    ctx: str,
) -> tuple[int, str, str | None, str | None, str] | None:
    """ACONTEXT → (fy, period_kind, basis, axis, period_scope). 형식 불일치는 None.

    예: CFY2025dHYA_..._ConsolidatedMember
        → (2025, duration, consolidated, None, "HYA")   ← 반기 누적
        CFY2025dHYQ_...  → scope "HYQ"                  ← 당기 3개월
    """
    m = _CTX.match(ctx or "")
    if not m:
        return None
    fy, ei, base, qual, rest = m.group(2), m.group(3), m.group(4), m.group(5), m.group(6) or ""
    scope = _canon_scope(base, qual)
    basis: str | None = None
    axis: str | None = None
    for token, b in (("ConsolidatedMember", CONSOLIDATED), ("SeparateMember", SEPARATE)):
        i = rest.find(token)
        if i >= 0:
            basis, axis = b, rest[i + len(token):].lstrip("_") or None
            break
    return int(fy), (INSTANT if ei == "e" else DURATION), basis, axis, scope


def _canon_scope(base: str, qual: str | None) -> str:
    """원시 기간코드 → models.SCOPE_* 정본 어휘.

    ACONTEXT의 분기 계열은 1분기 `FQ*` / 3분기 `TQ*` / `QT*`로 갈리지만 정본은
    누적(QTA)·당기(QTQ) 2종이다. 한정자 없는 접미(eHY·eTQ 등)는 시점값이라
    누적 쪽으로 보낸다 (instant에는 누적/당기 구분이 없다).
    """
    if base == "FY":
        return SCOPE_FY
    if base == "HY":
        return SCOPE_HYQ if qual == "Q" else SCOPE_HYA
    return SCOPE_QTQ if qual == "Q" else SCOPE_QTA


def _scope_of_month(month: int | None) -> str | None:
    """표 헤더의 기간 종료 월 → SCOPE_*. 12월=연간, 6월=반기 누적, 그 외 분기 누적."""
    if month is None:
        return None
    if month == 12:
        return SCOPE_FY
    return SCOPE_HYA if month == 6 else SCOPE_QTA


def _row_label(xml: str, pos: int) -> str | None:
    """같은 TR에서 값 셀보다 앞선 첫 셀 텍스트 = 행 라벨."""
    tr = xml.rfind("<TR", max(0, pos - 20000), pos)
    if tr < 0:
        return None
    c = _CELL.search(xml, tr, pos)
    return (_text(c.group(1)) if c else "") or None


def _assign_path(title: str, st: dict) -> tuple[str, int]:
    """법정 목차 주소 부여. 번호가 되돌아가면 하위 레벨로 내려가고, 상위 번호의
    다음 순번이면 그 레벨로 복귀한다 (주석 1..33 뒤의 `4. 재무제표` → III-4)."""
    rm = _ROMAN.match(title)
    if rm:
        st["root"], st["stack"] = rm.group(1), []
        return rm.group(1), 1
    dm = _DOTTED.match(title)
    if dm and st["stack"]:
        return f"{st['stack'][-1][1]}-{dm.group(2)}", len(st["stack"]) + 2
    nm = _NUMBERED.match(title)
    if nm and st["root"]:
        n, stack = int(nm.group(1)), st["stack"]
        while len(stack) >= 2 and n <= stack[-1][0] and n == stack[-2][0] + 1:
            stack.pop()
        if not stack:
            stack.append((n, f"{st['root']}-{n}"))
        elif n > stack[-1][0]:
            parent = stack[-2][1] if len(stack) >= 2 else st["root"]
            stack[-1] = (n, f"{parent}-{n}")
        else:
            stack.append((n, f"{stack[-1][1]}-{n}"))
        return stack[-1][1], len(stack) + 1
    st["pre"] += 1
    st["root"], st["stack"] = f"P{st['pre']}", []
    return st["root"], 1


def _build_sections(meta: DocMeta, xml: str) -> list[tuple[Section, int, int]]:
    marks = [(m.start(), m.end(), _text(m.group(1))) for m in _TITLE.finditer(xml)]
    marks = [m for m in marks if m[2]]
    state = {"root": None, "stack": [], "pre": 0}
    seen: set[str] = set()
    out: list[tuple[Section, int, int]] = []
    for i, (start, end, title) in enumerate(marks):
        stop = marks[i + 1][0] if i + 1 < len(marks) else len(xml)
        path, level = _assign_path(title, state)
        base, k = path, 1
        while path in seen:  # 목차 번호 중복 시 section_id 유일성 보장
            k += 1
            path = f"{base}~{k}"
        seen.add(path)
        text = _text(xml[end:stop])
        out.append((Section(
            section_id=f"{meta.doc_id}#{path}", doc_id=meta.doc_id, corp_code=meta.corp_code,
            path=path, title=title, level=level, text=text, char_len=len(text),
            tables_md=_tables_md(xml[end:stop]), content_class=_klass(title),
        ), start, stop))
    return out


def _xbrl_facts(meta: DocMeta, xml: str, ranges: list[tuple[Section, int, int]],
                units: list[str | None], upos: list[int], res: ParseResult) -> list[FinFact]:
    spos = [a for _, a, _ in ranges]
    facts: list[FinFact] = []
    bad_ctx = no_basis = 0
    for m in _TE.finditer(xml):
        attrs = m.group(1)
        cx = _ATTRS["ACONTEXT"].search(attrs)
        if not cx:
            continue
        parsed = _parse_context(cx.group(1))
        if not parsed:
            bad_ctx += 1
            continue
        raw = _text(m.group(2))
        if not raw or clean_number(raw) is None:
            continue
        fy, kind, basis, axis, scope = parsed
        if basis is None:
            basis, no_basis = CONSOLIDATED, no_basis + 1
        am = _ATTRS["ACODE"].search(attrs)
        acode = am.group(1) if am else None
        unit = _unit_at(units, upos, m.start())
        value, conf = to_krw(raw, unit)
        mdef = from_acode(acode)
        si = bisect.bisect_left(spos, m.start()) - 1
        facts.append(FinFact(
            doc_id=meta.doc_id, corp_code=meta.corp_code, fy=fy, period_kind=kind, basis=basis,
            label_ko=_row_label(xml, m.start()) or (acode or ""), raw_value=raw, source="xbrl",
            unit_confidence=conf, acode=acode, axis=axis, value_krw=value, raw_unit=unit,
            metric_key=mdef.key if mdef else None,
            src_section=ranges[si][0].path if si >= 0 else None,
            period_scope=scope,
        ))
    if bad_ctx:
        res.warn(f"ACONTEXT 파싱 실패 {bad_ctx}건 — fact 미생성")
    if no_basis:
        res.warn(f"ACONTEXT에 연결/별도 축 없음 {no_basis}건 — consolidated 기본값 적용")
    return facts


def _caption(xml: str, lo: int, upto: int) -> str:
    """표 직전의 `가./나.` 소제목. 없으면 직전 텍스트 꼬리로 폴백."""
    cap = ""
    for p in _PARA.finditer(xml, lo, upto):
        t = _text(p.group(1))
        if _SUBHEAD.match(t):
            cap = t
    return cap or _text(xml[max(lo, upto - 400):upto])[-120:]


def _table_facts(meta: DocMeta, xml: str, sec: Section, lo: int, hi: int, covered: set[str],
                 units: list[str | None], upos: list[int]) -> list[FinFact]:
    """요약재무정보 표 폴백. Stage B가 이미 커버한 지표는 건너뛴다."""
    facts: list[FinFact] = []
    emitted: set[tuple[str, int, str, str]] = set()
    seen_consol = False
    for tm in _TABLE.finditer(xml, lo, hi):
        cap = _caption(xml, lo, tm.start())
        if "연결" in cap:
            basis, seen_consol = CONSOLIDATED, True
        elif "별도" in cap or "개별" in cap:
            basis = SEPARATE
        else:
            basis = SEPARATE if seen_consol else CONSOLIDATED
        unit = _unit_at(units, upos, tm.start())
        fymap: dict[int, int] = {}
        for rm in _ROW.finditer(tm.group(1)):
            cs = _cells(rm.group(1))
            if len(cs) < 2:
                continue
            years = {i: int(y.group(1)) for i, c in enumerate(cs[1:], 1)
                     if (y := _YEAR.search(c))}
            if "구분" in cs[0] or (not fymap and len(years) >= 2):
                fymap = years or fymap
                continue
            mdef = from_label(cs[0])
            if mdef is None or mdef.key in covered:
                continue
            for i, cell in enumerate(cs[1:], 1):
                fy = fymap.get(i)
                if fy is None or clean_number(cell) is None:
                    continue
                key = (mdef.key, fy, basis, mdef.period_kind)
                if key in emitted:  # 첫 매칭 우선 (유동자산 → 비유동자산 오매칭 방어)
                    continue
                emitted.add(key)
                value, conf = to_krw(cell, unit)
                facts.append(FinFact(
                    doc_id=meta.doc_id, corp_code=meta.corp_code, fy=fy, basis=basis,
                    period_kind=mdef.period_kind, label_ko=cs[0], raw_value=cell,
                    unit_confidence=conf, source="table", metric_key=mdef.key,
                    value_krw=value, raw_unit=unit, src_section=sec.path,
                ))
    return facts


class PeriodicParser:
    """정기공시 파서. 메인 XML만 파싱하고 첨부는 warnings로 보고한다."""

    doc_group = PERIODIC

    def parse(self, meta: DocMeta, corpus_root: Path) -> ParseResult:
        res = ParseResult(meta=meta)
        try:
            folder = Path(corpus_root) / meta.file_path
            main = folder / f"{meta.rcept_no}.xml"
            if not main.is_file():
                res.warn(f"메인 XML 없음: {main}")
                return res
            for p in sorted(folder.glob("*.xml")):
                if p.name != main.name:
                    res.warn(f"첨부 미파싱: {p.name}")
            xml = main.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001 — 부분 실패는 warnings로 보고 (AC-P1)
            res.warn(f"파일 읽기 실패: {exc!r}")
            return res

        units: list[str | None] = []
        upos: list[int] = []
        ranges: list[tuple[Section, int, int]] = []
        try:
            units, upos = _decl_index(xml)
        except Exception as exc:  # noqa: BLE001
            res.warn(f"단위 인덱스 실패: {exc!r}")
        try:
            ranges = _build_sections(meta, xml)
            res.sections.extend(s for s, _, _ in ranges)
        except Exception as exc:  # noqa: BLE001
            res.warn(f"Stage A(목차) 실패: {exc!r}")
        try:
            res.fin_facts.extend(_xbrl_facts(meta, xml, ranges, units, upos, res))
        except Exception as exc:  # noqa: BLE001
            res.warn(f"Stage B(XBRL) 실패: {exc!r}")
        try:
            covered = {f.metric_key for f in res.fin_facts if f.metric_key}
            for sec, lo, hi in ranges:
                if _SUMMARY.search(sec.title):
                    res.fin_facts.extend(
                        _table_facts(meta, xml, sec, lo, hi, covered, units, upos))
        except Exception as exc:  # noqa: BLE001
            res.warn(f"Stage C(표 폴백) 실패: {exc!r}")
        return res
