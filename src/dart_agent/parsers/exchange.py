"""거래소공시(exchange) 파서 — ContractEvent + CorrectionDiff (SPEC T011).

🔴 인코딩 함정 (버그처럼 보이지만 의도된 코드다):
    이 문서들은 확장자가 `.xml`이지만 실제 내용은 **HTML**이고,
    `<meta charset=euc-kr>`을 선언하지만 **실제 바이트는 UTF-8**이다.
    1,469건 전부 `bytes.decode("euc-kr")`는 UnicodeDecodeError를 던지고
    `bytes.decode("utf-8")`은 성공한다 (실측).
    따라서 선언된 charset을 무시하고 UTF-8로 강제 디코딩한 뒤
    **문자열**을 BeautifulSoup에 넘긴다. bytes를 넘기면 lxml이 meta를
    재감지해 한글이 전부 깨진다 (`pandas.read_html(path)`도 같은 이유로 금지).

문서 구조: `<table><tr><td>라벨</td><td>값</td></tr>` key-value 행의 나열.
정정공시는 선행 블록(정정일자 / 정정관련 공시서류 / 정정사항 diff 표)이 추가되고,
본문에는 **정정 후** 값이 실린다 — 그래서 본문 표와 정정 표를 분리해 읽는다.
"""

from __future__ import annotations

import re
from pathlib import Path

from bs4 import BeautifulSoup

from ..models import EXCHANGE, ContractEvent, CorrectionDiff, DocMeta, ParseResult
from ..numbers import clean_number, to_krw

# doc_subtype → event_kind
_KIND_BY_SUBTYPE = {
    "단일판매공급계약체결": "체결",
    "단일판매공급계약해지": "해지",
    "신규시설투자등": "신규시설투자",
    "투자판단관련주요경영사항": "투자판단관련",
}
# doc_subtype이 없을 때 <title>/report_nm 스캔 — "해지"를 "체결"보다 먼저 본다
_KIND_BY_TITLE = (("해지", "해지"), ("신규시설투자", "신규시설투자"), ("시설투자", "신규시설투자"),
                  ("투자판단", "투자판단관련"), ("체결", "체결"))

# 필드별 라벨 후보 (앞선 후보가 우선). 서브타입 4종을 한 목록으로 흡수한다.
_K_CONTRACT_KIND = ("판매ㆍ공급계약구분", "판매ㆍ공급계약해지구분", "투자구분")
# `세부내용`은 SPEC 표기이나 본 코퍼스에 부재 → 실제 라벨을 후속 후보로 둔다 (실측 1,469건).
# `판매ㆍ공급계약내용`은 구분 없이 내용만 싣는 변종 레이아웃 (26건).
_K_DETAIL = ("세부내용", "체결계약명", "해지계약명", "투자대상", "제목",
             "판매ㆍ공급계약내용", "투자목적", "해지주요사유")
_K_COUNTERPARTY = ("계약상대",)
# 🔴 부분일치 키(`계약금액`)를 쓰면 변종 레이아웃의 `조건부 계약금액`(미확정분)을
#    총액보다 먼저 집어간다 — 반드시 완전한 라벨을 나열한다.
_K_AMOUNT = ("계약금액(원)", "해지금액(원)", "투자금액(원)", "계약금액총액(원)", "확정계약금액")
_K_REVENUE = ("최근매출액(원)",)  # 자기자본(원)은 매출액이 아니므로 매핑하지 않는다
_K_RATIO = ("매출액대비(%)", "자기자본대비(%)")
_K_DECISION = ("계약(수주)일자", "해지일자", "이사회결의일", "사실발생(확인)일", "결정일")
# 정정 표 판별 마커 — 이 중 하나라도 라벨에 있으면 그 표 전체를 정정 블록으로 본다
_CORR_MARKERS = ("정정일자", "정정관련공시서류", "정정사유", "정정사항", "정정항목")

_MISSING = {"", "-", "－", "—", "–", ".", "·", "해당사항없음"}
_DATE = re.compile(r"(\d{4})\s*[-./년]\s*(\d{1,2})\s*[-./월]\s*(\d{1,2})")


def _norm(text: str) -> str:
    """공백·선행번호·기호 제거 + 중점 통일 (`ㆍ` U+318D가 정본, `·`/`.`/`/`로 흔들림)."""
    s = re.sub(r"\s+", "", text or "")
    s = re.sub(r"^\d+[.)]", "", s)
    s = s.lstrip("-–—※*")
    s = re.sub(r"^\d+[.)]", "", s)
    for ch in "·・‧•．./":
        s = s.replace(ch, "ㆍ")
    return s


def _norm_date(text: str | None) -> str | None:
    """`2023-01-10` / `2023.1.10` / `2023년 1월 10일` → `20230110`."""
    if not text:
        return None
    m = _DATE.search(text)
    if m:
        y, mo, d = m.groups()
        return f"{y}{int(mo):02d}{int(d):02d}"
    m = re.search(r"(?<!\d)(\d{8})(?!\d)", text)
    return m.group(1) if m else None


def _cells(tr) -> list[str]:
    return [re.sub(r"\s+", " ", td.get_text(" ", strip=True)) for td in tr.find_all(["td", "th"])]


def _labels(cells: list[str]) -> list[str]:
    """라벨 위치 셀만 (마지막 셀 = 값). 라벨은 짧다 — 긴 본문 셀은 배제."""
    src = cells[:-1] if len(cells) > 1 else cells
    return [c for c in src if c and len(c) < 40]


def _find(rows: list[list[str]], keys: tuple[str, ...], exclude: str | None = None) -> str | None:
    """라벨 후보 순서대로 rows를 훑어 첫 유효 값을 반환. contains 매칭."""
    for key in keys:
        k = _norm(key)
        ex = _norm(exclude) if exclude else None
        for cells in rows:
            if len(cells) < 2:
                continue
            for cell in _labels(cells):
                label = _norm(cell)
                if k not in label or (ex and ex in label):
                    continue
                value = cells[-1].strip()
                if value and value not in _MISSING:
                    return value
    return None


def _diff_rows(rows: list[list[str]]) -> list[tuple[str | None, str | None, str | None]]:
    """`정정항목 | 정정전 | 정정후` 헤더 이후의 3열 행 → (item, before, after)."""
    out: list[tuple[str | None, str | None, str | None]] = []
    seen: set[tuple[str, str, str]] = set()
    started = False
    for cells in rows:
        labels = [_norm(c) for c in cells]
        if not started:
            started = any("정정항목" in label for label in labels)
            continue
        if len(cells) < 3:
            continue
        item, before, after = (c.strip() for c in cells[-3:])
        if (item, before, after) in seen:
            continue
        seen.add((item, before, after))
        out.append((item or None, before or None, after or None))
    return out


class ExchangeParser:
    """`raw/exchange/**` HTML → ContractEvent 1건 + CorrectionDiff 0..N건.

    파일 읽기만 하고 예외를 던지지 않는다 (AC-P1~P3).
    """

    doc_group = EXCHANGE

    def parse(self, meta: DocMeta, corpus_root: Path) -> ParseResult:
        result = ParseResult(meta=meta)
        body: list[list[str]] = []
        corr: list[list[str]] = []
        title = ""  # doc_subtype 부재 시 event_kind 폴백 소스
        path = Path(corpus_root) / meta.file_path / f"{meta.rcept_no}.xml"
        try:
            if not path.is_file():
                result.warn(f"{meta.doc_id}: 문서 파일 없음 — {path.name}")
            else:
                # 🔴 선언된 euc-kr을 무시하고 UTF-8로 디코딩 (모듈 docstring 참조)
                soup = BeautifulSoup(path.read_bytes().decode("utf-8", errors="replace"), "lxml")
                tag = soup.find("title")
                title = tag.get_text(" ", strip=True) if tag else ""
                body, corr = self._split_tables(soup)
                if not body:
                    result.warn(f"{meta.doc_id}: 본문 key-value 표를 찾지 못함")
        except Exception as exc:  # AC-P1 — 부분 결과 반환
            result.warn(f"{meta.doc_id}: HTML 파싱 실패 — {type(exc).__name__}: {exc}")

        result.contract_events.append(self._event(meta, title, body, corr, result))
        result.corrections.extend(self._corrections(meta, corr, result))
        return result

    @staticmethod
    def _split_tables(soup) -> tuple[list[list[str]], list[list[str]]]:
        """최상위 표를 본문/정정 블록으로 분리한다.

        diff 표에는 `정정전` 값이 들어 있어 본문과 섞으면 시작일/종료일이
        정정 **전** 값으로 오염된다. 중첩 표는 상위 표 순회에 이미 포함된다.
        """
        body: list[list[str]] = []
        corr: list[list[str]] = []
        for table in soup.find_all("table"):
            if table.find_parent("table") is not None:
                continue
            rows = [c for c in (_cells(tr) for tr in table.find_all("tr")) if any(c)]
            is_corr = any(m in _norm(cell) for r in rows for cell in _labels(r) for m in _CORR_MARKERS)
            (corr if is_corr else body).extend(rows)
        return body, corr

    @staticmethod
    def _event_kind(meta: DocMeta, title: str) -> str | None:
        kind = _KIND_BY_SUBTYPE.get(_norm(meta.doc_subtype or ""))
        if kind:
            return kind
        haystack = _norm(f"{title} {meta.report_nm}")
        for needle, mapped in _KIND_BY_TITLE:
            if needle in haystack:
                return mapped
        return None

    def _event(self, meta: DocMeta, title: str, body: list[list[str]],
               corr: list[list[str]], result: ParseResult) -> ContractEvent:
        kind = self._event_kind(meta, title)
        if kind is None:
            kind = meta.doc_subtype or "기타"
            result.warn(f"{meta.doc_id}: event_kind 판별 실패 — doc_subtype으로 대체")
        # 라벨이 `(원)`을 명시하므로 단위는 확정 — 추측이 아니다 (AC-U1)
        amount, _ = to_krw(_find(body, _K_AMOUNT), "원")
        revenue, _ = to_krw(_find(body, _K_REVENUE), "원")
        ratio = clean_number(_find(body, _K_RATIO))
        return ContractEvent(
            doc_id=meta.doc_id,
            corp_code=meta.corp_code,
            event_kind=kind,
            contract_kind=_find(body, _K_CONTRACT_KIND),
            detail=_find(body, _K_DETAIL),
            counterparty=_find(body, _K_COUNTERPARTY),
            amount_krw=amount,
            recent_revenue_krw=revenue,
            ratio_pct=None if ratio is None else float(ratio),
            start_dt=_norm_date(_find(body, ("시작일",))),
            end_dt=_norm_date(_find(body, ("종료일",))),
            decision_dt=_norm_date(_find(body, _K_DECISION))
            or _norm_date(_find(corr, ("정정일자",))),
        )

    @staticmethod
    def _corrections(meta: DocMeta, corr: list[list[str]],
                     result: ParseResult) -> list[CorrectionDiff]:
        if not corr and not meta.is_correction:
            return []
        # `정정관련공시서류`는 `정정관련공시서류제출일`의 부분문자열이라 제출일 행을 배제한다
        target = _find(corr, ("정정관련공시서류",), exclude="제출일")
        submit = _norm_date(_find(corr, ("정정관련공시서류제출일",)))
        if target is None or submit is None:
            result.warn(f"{meta.doc_id}: 정정 원본 포인터 불완전 (서류={target}, 제출일={submit})")
        base = {
            "doc_id": meta.doc_id,
            "target_doc_kind": target,
            "target_submit_dt": submit,
            "reason": _find(corr, ("정정사유",)),
        }
        diffs = _diff_rows(corr)
        if not diffs:
            # 항목이 없어도 체인 포인터는 살려야 한다 — 정정↔원본 연결의 유일한 근거
            return [CorrectionDiff(**base)]
        return [
            CorrectionDiff(**base, item=item, before_val=before, after_val=after)
            for item, before, after in diffs
        ]
