"""주요사항보고서(major) 파서 — CapitalEvent 1건 + (정정 시) CorrectionDiff (SPEC §1-1).

문서 유형은 <DOCUMENT-NAME>의 괄호 문구가 정본이다 (report_nm은 보조 소스). 25종 이상의
하위 유형을 사용자 질의 어휘(유상증자/CB/BW/EB…)로 정규화하고, 미매핑 유형은 원문을 그대로
두고 warning을 남긴다 — 조용히 사라지지 않게 한다.

금액 셀 위치는 유형마다 다르다. ACLASS는 서식 코드(TBL_SEL_STK/CCS_PUB/CB_PUB…)이지
추출 표식이 아니므로 표를 선별하지 않고 전수 순회한다 (598건 실측: 처분예정금액 157건이
TBL_SEL_STK, 소송가액 4건이 NORMAL 표 — ACLASS 필터는 과반을 놓친다).

단위는 라벨의 '(원)' 표기 → 표의 '단위' 선언 순으로만 확정하고 실패 시 amount_krw는
None으로 둔다 — 배율 추측 금지 (AC-U1). 예외는 올리지 않고 warnings로 보고한다 (AC-P1).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..models import MAJOR, CapitalEvent, CorrectionDiff, DocMeta, ParseResult
from ..numbers import UNIT_SCALES, clean_number, detect_unit, to_krw

_DOCNAME = re.compile(r"<DOCUMENT-NAME\b[^>]*>(.*?)</DOCUMENT-NAME>", re.S)
_CORRECTION = re.compile(r"<CORRECTION>.*?</CORRECTION>", re.S)
_CORR_BLOCK = re.compile(r"<CORRECTION>(.*?)</CORRECTION>", re.S)
_TABLE = re.compile(r"<TABLE\b[^>]*>(.*?)</TABLE>", re.S)
_ROW = re.compile(r"<TR\b[^>]*>(.*?)</TR>", re.S)
_CELL = re.compile(r"<T([DEU])\b([^>]*)>(.*?)</T\1>", re.S)
_TH = re.compile(r"<TH\b[^>]*>(.*?)</TH>", re.S)
_TD = re.compile(r"<TD\b[^>]*>(.*?)</TD>", re.S)
_AUNITVALUE = re.compile(r'\bAUNITVALUE="([^"]*)"')

_TAG = re.compile(r"<[^>]+>")
_YMD8 = re.compile(r"^\d{8}$")
# 두 번째 구분자에 '년'을 허용한다 — 원문 오타 '2025년 08년 28일' 실측 1건 (미래에셋증권).
_KO_DATE = re.compile(r"(\d{4})\s*[년./-]\s*(\d{1,2})\s*[월년./-]\s*(\d{1,2})")
_NUMBERED = re.compile(r"^\s*\d+[-.\d]*\s*[.:]")
_LEAD_NO = re.compile(r"^\d+[-.\d]*\s*[.:]\s*")
_PARENS = re.compile(r"[(（]\s*(?:단\s*위\s*[:：]?)?\s*([^()（）]{0,6}?)\s*[)）]")

# 정정 헤더는 두 서식이 공존한다 — <P> 인라인, 또는 표 1행(라벨 TD + 값 TD). 표를 텍스트로
# 평탄화하면 둘 다 '1. 정정대상 공시서류 : …' 형태가 되어 같은 패턴으로 잡힌다.
_TAIL = r"(?:\s\d{1,2}\s*[.]\s|$)"
_C_KIND = re.compile(r"정정(?:대상|관련)\s*(?:공시서류|보고서)[^:：]{0,4}[:：]\s*(.{1,80}?)" + _TAIL)
# 날짜엔 종료 경계를 두지 않는다 — 뒤에 정정이력 표가 붙는 서식에서 매칭 자체가 실패한다.
# 경계는 _ymd()의 날짜 검색이 대신한다.
_C_DT = re.compile(r"(?:최초\s*제출일|제출일자)[^:：]{0,6}[:：]\s*(.{1,40})")
_C_REASON = re.compile(r"정정\s*사유\s*[:：]\s*(.{1,300}?)" + _TAIL)

# 접수 서식 상단 보일러플레이트 — detail_json 노이즈 제거용
_BOILER = ("회사명", "대표이사", "본점소재지", "작성책임자", "금융위원회", "전화", "홈페이지")

# 원문 포함 문구 → 정규화 event_kind. 구체적인 것이 먼저 와야 한다
# (신탁계약체결/해지 → 자기주식취득, 회사분할합병 → 회사분할).
_KIND_MAP: tuple[tuple[str, str], ...] = (
    ("자기주식취득신탁계약체결", "자기주식취득신탁체결"),
    ("자기주식취득신탁계약해지", "자기주식취득신탁해지"),
    ("회사분할합병", "분할합병"),
    ("유상증자", "유상증자"), ("무상증자", "무상증자"),
    ("전환사채", "전환사채(CB)"), ("신주인수권부사채", "신주인수권부사채(BW)"),
    ("교환사채", "교환사채(EB)"),
    ("조건부자본증권", "조건부자본증권"), ("자본으로인정되는채무증권", "조건부자본증권"),
    ("자기주식취득", "자기주식취득"), ("자기주식처분", "자기주식처분"),
    ("회사합병", "합병"), ("회사분할", "분할"), ("감자", "감자"),
    ("주식교환", "주식교환·이전"), ("주식이전", "주식교환·이전"),
    ("타법인주식및출자증권양수", "타법인주식양수"),
    ("타법인주식및출자증권양도", "타법인주식양도"),
    ("소송", "소송제기"),
    ("영업양수", "영업양수도"), ("영업양도", "영업양수도"),
)

# 금액 라벨 우선순위 — 앞선 라벨이 이긴다 (SPEC). 실측 보강: 영업양수도는 '양수가액(원)',
# 유형자산양수도는 '자산총액(원)'을 쓴다 (SPEC 표에 없던 표기). '자산총액'은 최후순위 —
# 합병/분할의 '자산총계'와 표기가 달라 충돌하지 않는다.
_AMOUNT_LABELS = (
    "사채의권면(전자등록)총액", "사채의권면총액", "권면총액",
    "신주발행가액총액", "자금조달금액", "증자금액",
    "취득예정금액", "처분예정금액", "계약금액",
    "양수금액", "양도금액", "양수가액", "양도가액",
    "소송가액", "자산총액",
)
_DATE_LABELS = ("이사회결의일", "취득결정일", "결정일자", "계약체결", "결정일", "제기일자")


def _text(raw: str) -> str:
    """중첩 태그(<SPAN>/<P> 등) 제거 + 공백 정규화."""
    return re.sub(r"\s+", " ", _TAG.sub(" ", raw)).strip()


def _squash(raw: str) -> str:
    return re.sub(r"\s+", "", raw)


def _ymd(value: str | None) -> str | None:
    """YYYYMMDD 또는 '2024년 11월 18일' / '2024-11-18' → YYYYMMDD. 실패 시 None."""
    v = (value or "").strip()
    if _YMD8.match(v):
        return v
    m = _KO_DATE.search(v)
    return f"{m.group(1)}{int(m.group(2)):02d}{int(m.group(3)):02d}" if m else None


def _unit_in(text: str) -> str | None:
    """라벨 괄호에서 통화 단위를 찾는다. '(전자등록)'/'(주)'/'(%)'는 걸러진다."""
    for m in _PARENS.finditer(text or ""):
        cand = _squash(m.group(1))
        if cand in UNIT_SCALES:
            return cand
    return None


def _scan_rows(xml: str) -> list[dict]:
    """표를 전수 순회해 라벨→값 행 목록을 만든다 (문서 순서 보존).

    선행 TD들이 라벨, 이후 TE/TU가 값이다. ROWSPAN으로 라벨이 생략된 후속 행은 직전
    번호 라벨('2. 취득예정금액(원)')을 접두로 붙여 키 충돌을 막는다.
    """
    rows: list[dict] = []
    for tbl in _TABLE.finditer(xml):
        block = tbl.group(1)
        table_unit = detect_unit(_text(block))
        main = ""
        for tr in _ROW.finditer(block):
            cells = [
                (m.group(1), m.group(2), _text(m.group(3))) for m in _CELL.finditer(tr.group(1))
            ]
            cells = [c for c in cells if c[2]]
            if len(cells) < 2:
                continue
            i = 0
            while i < len(cells) and cells[i][0] == "D":
                i += 1
            labels, values = [c[2] for c in cells[:i]], cells[i:]
            if not values:  # 값 셀까지 TD인 표 — 마지막 셀을 값으로 본다
                values, labels = [cells[-1]], labels[:-1]
            if not labels or any(b in _squash(labels[0]) for b in _BOILER):
                continue
            if _NUMBERED.match(labels[0]):
                main = labels[0]
            elif main:
                labels = [main] + labels
            key = " / ".join(labels)
            rows.append(
                {
                    "k": key,
                    "v": " | ".join(v[2] for v in values),
                    "nums": [v[2] for v in values if clean_number(v[2]) is not None],
                    "av": [m.group(1) for v in values for m in [_AUNITVALUE.search(v[1])] if m],
                    "unit": _unit_in(key) or table_unit,
                }
            )
    return rows


def _amount(rows: list[dict]) -> tuple[int | None, str | None]:
    """(원 단위 금액, 미확정 사유). 단위 미확정이면 값을 만들지 않는다 (AC-U1).

    라벨 접두 일치를 먼저 시도한다 — '5. 신탁계약의 계약금액'(취득한도 표)이
    '1. 계약금액(원)'(본 계약금액)을 가로채지 않게 하는 것이 목적이다.
    """
    keyed = [
        (_LEAD_NO.sub("", _squash(r["k"])), r)
        for r in rows
        # 비율 행은 금액이 아니다 — '자산총액 대비(%)'가 '자산총액'으로 잡히는 것을 막는다.
        if not any(x in _squash(r["k"]) for x in ("(%)", "비율", "대비"))
    ]
    unresolved: str | None = None
    for exact in (True, False):
        for label in _AMOUNT_LABELS:
            for key, row in keyed:
                if not (key.startswith(label) if exact else label in key) or not row["nums"]:
                    continue
                krw, _ = to_krw(row["nums"][0], row["unit"])
                if krw is not None:
                    return krw, None
                unresolved = unresolved or f"단위 미확정: {row['k']!r} = {row['nums'][0]!r}"
    # 폴백 — '자금조달의 목적' 항목별 합계. 유상증자는 총액 라벨이 없고 이 표만 갖는다
    # (실측: 시설/운영자금 합계 = 신주수 x 발행가액과 일치).
    total, seen = 0, False
    for row in rows:
        if "자금조달의목적" not in _squash(row["k"]) or not row["nums"]:
            continue
        krw, _ = to_krw(row["nums"][0], row["unit"])
        if krw is not None:
            total, seen = total + krw, True
    return (total, None) if seen else (None, unresolved or "금액 라벨 없음")


def _decision_dt(rows: list[dict]) -> str | None:
    for label in _DATE_LABELS:
        for row in rows:
            if label not in _squash(row["k"]):
                continue
            for cand in row["av"] + [row["v"]]:
                if got := _ymd(cand):
                    return got
    return None


def _event_kind(xml: str, report_nm: str) -> tuple[str, str | None]:
    """(event_kind, 미매핑 경고). DOCUMENT-NAME 괄호 문구가 정본."""
    name = _DOCNAME.search(xml)
    subject = ""
    if name:
        inner = re.search(r"[(（](.*)[)）]", _text(name.group(1)), re.S)
        subject = _text(inner.group(1)) if inner else _text(name.group(1))
    if not subject:
        subject = re.sub(r"^\[[^\]]*\]|주요사항보고서", "", report_nm).strip("()（） ")
    squashed = _squash(subject)
    for needle, kind in _KIND_MAP:
        if needle in squashed:
            return kind, None
    return subject, f"unmapped event subtype: {subject!r} — event_kind를 원문 그대로 사용"


def _corrections(xml: str, doc_id: str) -> list[CorrectionDiff]:
    """정정 블록의 원본 포인터 + 항목별 정정전/정정후."""
    block = _CORR_BLOCK.search(xml)
    body = block.group(1) if block else xml

    def is_diff(tbl: re.Match[str]) -> bool:
        heads = [_squash(_text(h)) for h in _TH.findall(tbl.group(1))]
        return any("정정전" in h for h in heads) and any("정정후" in h for h in heads)

    diff = next((t for t in _TABLE.finditer(body) if is_diff(t)), None)
    # 대조표만 잘라낸다 — 헤더가 표 1행인 서식이 있어 표 전체를 버리면 포인터를 놓친다.
    lead = _text(body[: diff.start()] if diff else body)
    kind, dt, rsn = _C_KIND.search(lead), _C_DT.search(lead), _C_REASON.search(lead)
    base = {
        "doc_id": doc_id,
        "target_doc_kind": (kind.group(1).strip() or None) if kind else None,
        "target_submit_dt": _ymd(dt.group(1)) if dt else None,
    }
    global_reason = (rsn.group(1).strip() or None) if rsn else None
    if diff is None:  # 항목별 대조표가 없어도 정정 사실 자체는 남긴다
        return [CorrectionDiff(**base, reason=global_reason, item=None)]

    heads = [_squash(_text(h)) for h in _TH.findall(diff.group(1))]

    def col(*names: str) -> int | None:
        return next((i for i, h in enumerate(heads) if any(n in h for n in names)), None)

    i_item, i_rsn, i_bef, i_aft = col("항목"), col("정정사유"), col("정정전"), col("정정후")
    rows: list[CorrectionDiff] = []
    for tr in _ROW.finditer(diff.group(1)):
        tds = [_text(t) for t in _TD.findall(tr.group(1))]
        if len(tds) < 2 or not any(tds):
            continue
        if len(tds) == len(heads) and None not in (i_item, i_bef, i_aft):
            item, before, after = tds[i_item], tds[i_bef], tds[i_aft]
            reason = tds[i_rsn] if i_rsn is not None else None
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


class MajorParser:
    """주요사항보고서 1건 → CapitalEvent 1건 (+ 정정 시 CorrectionDiff)."""

    doc_group = MAJOR

    def parse(self, meta: DocMeta, corpus_root: Path) -> ParseResult:
        result = ParseResult(meta=meta)
        path = Path(corpus_root) / meta.file_path / f"{meta.rcept_no}.xml"
        try:
            xml = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            result.warn(f"read failed: {path.name} ({exc})")
            return result

        kind = ""
        try:
            kind, unmapped = _event_kind(xml, meta.report_nm)
            if unmapped:
                result.warn(unmapped)
            rows = _scan_rows(_CORRECTION.sub(" ", xml))
            amount, why = _amount(rows)
            if why:
                result.warn(f"amount_krw 미산출 — {why}")
            result.capital_events.append(
                CapitalEvent(
                    doc_id=meta.doc_id,
                    corp_code=meta.corp_code,
                    event_kind=kind or meta.report_nm,
                    amount_krw=amount,
                    decision_dt=_decision_dt(rows) or meta.rcept_dt,
                    detail_json=json.dumps({r["k"]: r["v"] for r in rows}, ensure_ascii=False),
                )
            )
        except Exception as exc:  # 파서는 예외를 올리지 않는다 (AC-P1)
            result.warn(f"capital event failed: {type(exc).__name__}: {exc}")
            if not result.capital_events:  # event_kind는 반드시 1건 남긴다
                result.capital_events.append(
                    CapitalEvent(
                        doc_id=meta.doc_id,
                        corp_code=meta.corp_code,
                        event_kind=kind or meta.report_nm,
                        decision_dt=meta.rcept_dt,
                    )
                )
        if meta.is_correction:
            try:
                result.corrections.extend(_corrections(xml, meta.doc_id))
            except Exception as exc:
                result.warn(f"correction diff failed: {type(exc).__name__}: {exc}")
        return result
