"""검증기 V1~V5 — 전부 결정론, LLM 호출 0건 (SPEC §5 AC-V1~V3).

🔴 검증자가 LLM이면 환각을 환각으로 검증한다. 그래서 여기는 정규식·집합 연산만 쓴다.

담당 평가지표:
  V1 근거 기반(4)·정확성(1) — 답변의 모든 수치가 근거에서 왔는가
  V2 근거 완전성(2)         — 인용 마커가 실재하는가
  V3 요구사항 충족(3)       — 질의 요구 항목이 답변에 다 있는가
  V4 안전성(6)              — 미래예측·투자의견 금지
  V5 정보한계 대응(7)       — abstention 판정 연동
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# 금지 표현 (V4) — 과제 금지 조항: 공시에 근거 없는 미래 예측·투자 의견
# 근거에 없는 주요 평가·변화 단정은 숫자가 없어도 환각이다.
# 보수적으로 금융 분석에서 자주 악용되는 서술만 검사한다.
UNSUPPORTED_ASSERTION_TERMS: tuple[tuple[str, str], ...] = (
    (r"개선|회복|악화|호조|부진|증가세|감소세|상승세|하락세", "근거 없는 성과/추세 단정"),
    (r"경쟁력이\s*(있|높)|성장성이\s*(있|높)|긍정적|부정적", "근거 없는 평가"),
)

FORBIDDEN_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"목표\s*주가", "목표주가 제시"),
    (r"(매수|매도|비중\s*확대|비중\s*축소)\s*(를|을)?\s*(추천|권장|의견|제시)", "투자의견 제시"),
    (r"(투자|매수|매도)\s*를?\s*(추천|권(유|장))", "투자 권유"),
    (r"(주가|실적|매출|영업이익)(이|가)?\s*(오를|내릴|상승할|하락할|증가할|감소할)\s*(것|전망|가능성)",
     "미래 예측"),
    (r"(향후|앞으로|내년|차년도|다음\s*분기)\s*[^.。\n]{0,30}(전망|예상|예측|기대)(된|됩니다|한다|합니다)",
     "미래 전망"),
    (r"유망\s*(하다|합니다|한\s*종목)", "투자 유망 판단"),
    (r"저평가\s*(되어|돼|상태)", "밸류에이션 의견"),
)

# 숫자 토큰 (V1) — 한국어 금액/비율 표기 포함 (AC-V3)
_NUM_TOKEN = re.compile(
    r"(?<![\w])"
    r"(?:\d{1,3}(?:,\d{3})+|\d+)"        # 1,234,567 또는 1234
    r"(?:\.\d+)?"                          # 소수
    r"\s*(?:조원|억원|만원|백만원|천원|원|%|퍼센트|주|배)?"
)
# 답변에서 검증 면제되는 숫자: 연도·기수·분기·인용마커·목차주소
# 🔴 순서가 중요하다 — 긴 패턴(날짜)이 짧은 패턴(연도)보다 먼저 와야 한다.
#    연도가 먼저 매칭되면 "2026-03-31"에서 "2026"만 지워지고 "03"/"31"이 수치로 새어나간다.
_EXEMPT = re.compile(
    r"\d{4}-\d{1,2}-\d{1,2}"           # 날짜 2026-03-31
    r"|\d{4}\.\d{1,2}(?:\.\d{1,2})?"   # 날짜 2026.03 / 2026.03.31
    r"|\d{4}\s*년\s*\d{1,2}\s*월(?:\s*\d{1,2}\s*일)?"  # 2024년 11월 18일
    r"|\b\d{8}\b"                       # 20260331
    r"|(?:19|20)\d{2}\s*년?"           # 연도
    r"|제\s*\d+\s*기"                   # 제56기
    r"|\d\s*분기|[1-4]Q"               # 분기
    r"|\[C\d+\]"                       # 인용 마커
    r"|\b[IVX]+(?:-\d+)*\b"           # 목차 주소 III-2-2
)
_CITE = re.compile(r"\[C(\d+)\]")
_PLACEHOLDER = re.compile(r"\{\{[^}]+\}\}")


@dataclass
class VerifyReport:
    ok: bool = True
    v1_ungrounded_numbers: list[str] = field(default_factory=list)
    v2_missing_citations: list[str] = field(default_factory=list)
    v3_unmet_requirements: list[str] = field(default_factory=list)
    v4_forbidden: list[str] = field(default_factory=list)
    v5_unresolved_placeholders: list[str] = field(default_factory=list)

    def failures(self) -> list[str]:
        out = []
        if self.v1_ungrounded_numbers:
            out.append(f"V1 근거없는 수치 {len(self.v1_ungrounded_numbers)}건: "
                       f"{self.v1_ungrounded_numbers[:5]}")
        if self.v2_missing_citations:
            out.append(f"V2 존재하지 않는 인용 {self.v2_missing_citations}")
        if self.v3_unmet_requirements:
            out.append(f"V3 미충족 요구사항 {self.v3_unmet_requirements}")
        if self.v4_forbidden:
            out.append(f"V4 금지표현 {self.v4_forbidden}")
        if self.v5_unresolved_placeholders:
            out.append(f"V5 미치환 자리표시자 {self.v5_unresolved_placeholders}")
        return out

    def summary(self) -> str:
        return "검증 통과" if self.ok else " · ".join(self.failures())


def _norm_num(tok: str) -> str:
    """비교용 정규화 — 콤마·공백·단위 제거."""
    return re.sub(r"[,\s]|조원|억원|만원|백만원|천원|원|%|퍼센트|주|배", "", tok)


def extract_numbers(text: str) -> list[str]:
    """검증 대상 숫자만 추출 (연도·기수·인용마커·날짜 제외)."""
    masked = _EXEMPT.sub(" ", text or "")
    return [m.group(0).strip() for m in _NUM_TOKEN.finditer(masked) if _norm_num(m.group(0))]


def verify(
    answer: str,
    *,
    context: str,
    citation_ids: set[str],
    requirements: list[str],
    grounded_values: set[str] | None = None,
) -> VerifyReport:
    """답변을 근거에 대조 검증한다. 순수 함수 (AC-V2).

    grounded_values: fact 슬롯 치환으로 주입된 값들의 정규화 문자열 집합.
    context: retrieved_context 원문 (여기 등장하는 숫자는 근거 있음으로 인정).
    """
    rep = VerifyReport()
    answer = answer or ""
    ctx_norm = _norm_num(context or "")
    grounded = {_norm_num(v) for v in (grounded_values or set())}

    # V1 — 모든 수치가 근거에서 왔는가
    for tok in extract_numbers(answer):
        n = _norm_num(tok)
        if not n:
            continue
        if n in grounded or n in ctx_norm:
            continue
        rep.v1_ungrounded_numbers.append(tok)

    # V2 — 인용 마커가 실재하는가
    for m in _CITE.finditer(answer):
        cid = f"C{m.group(1)}"
        if cid not in citation_ids:
            rep.v2_missing_citations.append(cid)

    # V3 — 요구사항별 대응 문장 존재
    for req in requirements or []:
        if not _requirement_met(req, answer):
            rep.v3_unmet_requirements.append(req)

    # V4 — 금지 표현 및 근거 없는 비수치 단정
    for pat, label in FORBIDDEN_PATTERNS:
        if re.search(pat, answer):
            rep.v4_forbidden.append(label)
    ctx_text = context or ""
    for pat, label in UNSUPPORTED_ASSERTION_TERMS:
        if re.search(pat, answer) and not re.search(pat, ctx_text):
            rep.v4_forbidden.append(label)

    # V5 — 미치환 자리표시자 (D1 슬롯 바인딩 실패 = 생성 실패)
    rep.v5_unresolved_placeholders = [m.group(0) for m in _PLACEHOLDER.finditer(answer)]

    rep.ok = not (
        rep.v1_ungrounded_numbers
        or rep.v2_missing_citations
        or rep.v3_unmet_requirements
        or rep.v4_forbidden
        or rep.v5_unresolved_placeholders
    )
    return rep


def _requirement_met(req: str, answer: str) -> bool:
    """요구사항 키워드가 답변에 반영되었는지 (보수적: 핵심 토큰 과반 등장)."""
    tokens = [t for t in re.split(r"[\s,·/]+", req) if len(t) >= 2]
    if not tokens:
        return True
    hit = sum(1 for t in tokens if t in answer)
    return hit >= max(1, len(tokens) // 2)


def strip_failing_sentences(answer: str, rep: VerifyReport, context: str | None = None) -> str:
    """V1/V4 위반 문장을 제거한다 (재생성 실패 시 최후 수단).

    문장 단위로 자르고, 근거 없는 수치나 금지 표현을 포함한 문장만 버린다.

    🔴 `context`를 반드시 넘길 것 — 없으면 근거 있는 서술까지 지운다.
       UNSUPPORTED_ASSERTION_TERMS는 verify()에서 **문맥에 없을 때만** 위반이다.
       여기서 그 조건을 빼면 제거기가 판정기보다 엄격해져서, 다른 문장의 V1 위반
       하나 때문에 verify가 통과시킨 "부진"·"증가세" 문장이 통째로 사라진다
       (2026-09-03 재현). 근거 있는 서술 삭제는 정확성·근거 완전성·요구사항 충족
       세 지표를 동시에 깎는다.
    """
    bad_nums = {_norm_num(x) for x in rep.v1_ungrounded_numbers}
    ctx_text = context or ""
    # 문맥에 이미 등장하는 서술 용어는 근거가 있으므로 제거 대상에서 뺀다.
    assertion_pats = [p for p, _ in UNSUPPORTED_ASSERTION_TERMS
                      if not re.search(p, ctx_text)]
    sentences = re.split(r"(?<=[.。!?])\s+|\n+", answer or "")
    keep: list[str] = []
    for s in sentences:
        if not s.strip():
            continue
        if any(_norm_num(t) in bad_nums for t in extract_numbers(s)):
            continue
        if any(re.search(p, s) for p, _ in FORBIDDEN_PATTERNS):
            continue
        if any(re.search(p, s) for p in assertion_pats):
            continue
        keep.append(s.strip())
    return " ".join(keep)
