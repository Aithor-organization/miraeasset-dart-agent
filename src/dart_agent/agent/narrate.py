"""HCX 서술 계층 — 결정론 답변을 자연스러운 문장으로 다듬는다.

🔴 **이 계층은 수치를 만들지 않는다.**

설계 D1(수치 결정론)의 뒷 절반이다. 앞 절반(사실 확정)은 SQL이 끝냈고, 여기서는
**이미 확정된 문장을 읽기 좋게 고쳐 쓰기만** 한다. LLM에게 주는 것은 완성된 답변이지
원자료가 아니므로, 계산하거나 조회할 여지 자체가 없다.

안전장치 3겹 — 하나라도 걸리면 **원본 템플릿을 그대로 반환**한다:

  1. `LLMResponse.usable`  — 빈 응답·추론 절단 감지 (HCX-007 thinking 함정)
  2. 숫자 집합 동일성      — 서술본의 수치가 원본과 **정확히 같아야** 한다
  3. 호출자 측 V1~V5      — 이 모듈이 통과시켜도 검증기가 다시 본다

2번이 핵심이다. LLM이 "약 300조원"처럼 반올림하거나 자릿수를 흘리면 즉시 버린다 —
읽기 좋아지는 것보다 **틀리지 않는 것**이 우선이다 (D5).
"""

from __future__ import annotations

import logging
import re

from ..llm.provider import LLMProvider

log = logging.getLogger(__name__)

# 답변에서 비교할 수치. 검증기 V1과 같은 기준을 쓴다 —
# 연도·분기·날짜·섹션주소는 서식이라 비교 대상이 아니다.
_DATE = re.compile(r"20\d{2}[-.]\d{1,2}([-.]\d{1,2})?|20\d{6}")
_YEAR = re.compile(r"20\d{2}\s*년|제\s*\d+\s*기|\d\s*분기|\d{1,2}\s*월|\d{1,2}\s*일")
_CITE = re.compile(r"\[C\d+\]")
_SECTION = re.compile(r"\b[IVX]+(-\d+)+\b|\bP\d+(-\d+)*\b")
_NUM = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
# 🔴 숫자만 비교하면 **단위 변조를 못 잡는다**. 실측: `17,569,457,486천원`을
#    `17,569,457,486백만원`으로 바꿔놨는데 자릿수가 같아 검사를 통과했다 —
#    값이 1000배 틀린 답이 나갔다. 그래서 (숫자, 단위) 쌍으로 비교한다.
_UNIT = r"(?:천원|백만원|십억원|억원|조원|만원|원|주|%|퍼센트|배|건|명|개|포인트|bp)"
_NUM_UNIT = re.compile(rf"(-?\d[\d,]*(?:\.\d+)?)\s*({_UNIT})?")
# 줄머리 목록 번호("1. ", "2) ")는 수치가 아니라 서식이다. 실제 금액이
# 줄머리에 홀로 오는 경우는 없으므로 안전하게 제외할 수 있다.
_LIST_MARKER = re.compile(r"^\s*\d{1,2}[.)]\s+", re.M)
# 정확한 값 바로 뒤 괄호 안의 사람용 환산 표기 — "…903백만원(300.9조원)"
_DERIVED = re.compile(
    rf"\d[\d,]*(?:\.\d+)?\s*[가-힣]*원?\s*\(\s*(-?\d[\d,]*(?:\.\d+)?)\s*({_UNIT})")

# 🔴 프롬프트로 금지했지만 LLM은 지시를 어긴다 — 아래는 실제로 관측된 위반이다.
#    지시 준수에 의존하면 조용히 뚫리므로 **결정론적으로 거부**한다.
#
#    (1) 메타 주석: "(수정 사항: … 변경하여 …)" 를 답변 본문에 붙였다.
#        HCX-007은 이 습관이 완강해서 프롬프트로 못 막았다 — **답변 뒤에** 붙이므로
#        잘라내면 앞의 본문은 그대로 쓸 수 있다. 거부보다 절단이 채택률에 유리하다.
_META = re.compile(r"(수정\s*사항|변경\s*사항|다듬은\s*답변|위\s*답변을|간결성을\s*위해|"
                   r"주요\s*변경|개선\s*사항|참고\s*사항)")
_META_HEAD = re.compile(
    r"\n\s*[\(\[]?\s*(?:🔹|▶|—|-|\*)?\s*"
    r"(?:수정\s*사항|변경\s*사항|주요\s*변경|개선\s*사항|참고\s*사항|"
    r"다듬은\s*답변|위\s*답변을|간결성을\s*위해)"
)
#    (2) 확정 사실의 추측화: 공시 확정치를 "예상됩니다"로 바꿨다.
#        원본에 없던 표현이 서술본에만 나타나면 거부한다 (원본에 있으면 정당한 인용).
_HEDGE = re.compile(
    r"예상|추정|전망|보입니다|듯|가능성이\s*(있|높|낮)|것으로\s*(보|판단|평가)|"
    r"할\s*(수도|지도)|아마|대략|약\s*\d"
)

SYSTEM = """너는 공시 데이터 분석 결과를 다듬는 편집자다.

규칙:
1. 숫자를 절대 바꾸지 마라. 반올림·단위변환·생략 금지.
   "300,870,903백만원"을 "약 300조"로 바꾸면 안 된다. 원문 그대로 유지한다.
2. 인용 표기 [C1] [C2]는 원래 위치에 그대로 둔다.
3. 새로운 사실을 추가하지 마라. 주어진 내용만 다시 쓴다.
4. 주어진 값은 **공시에 확정 기재된 사실**이다. 단정형으로 서술한다.
   "~입니다" / "~로 집계되었습니다" (O)
   "~로 예상됩니다" / "~로 추정됩니다" / "~일 것으로 보입니다" (X — 확정 사실을 추측으로 바꾸지 마라)
5. 추측·전망·투자의견을 넣지 마라.
6. 한국어로 2~4문장. 조사와 어미를 자연스럽게 다듬는 것이 주 임무다.

🔴 출력 형식: **다듬은 답변 본문만** 출력한다.
   무엇을 왜 고쳤는지 설명하지 마라. "(수정 사항: …)" 같은 주석을 붙이지 마라.
   머리말·맺음말·추론 과정 없이 답변 문장만."""


def _numbers(text: str) -> set[str]:
    """`수치|단위` 키 집합을 뽑는다 (연도·날짜·인용·섹션주소·목록번호 제외).

    단위를 키에 넣는 이유는 실측 사고 때문이다 — `…486천원`을 `…486백만원`으로
    바꿔도 자릿수가 같아 숫자만 비교하면 통과한다. **1000배 틀린 답**이 나간다.
    """
    t = _CITE.sub(" ", text)
    t = _SECTION.sub(" ", t)
    t = _DATE.sub(" ", t)
    t = _YEAR.sub(" ", t)
    t = _LIST_MARKER.sub(" ", t)
    return {f"{m.group(1).replace(',', '')}|{m.group(2) or ''}"
            for m in _NUM_UNIT.finditer(t)}


def _display_aliases(body: str) -> set[str]:
    """정확한 값 뒤에 붙는 **파생 표기**를 모은다 — `…903백만원(300.9조원)`의 `300.9`.

    LLM은 이 괄호를 자주 떨어뜨린다(전체 폴백의 최다 원인). 하지만 이건
    이미 답변에 남아 있는 정확한 값을 사람이 읽기 좋게 환산한 것이므로,
    잃어도 **거짓이 생기지 않는다** — 읽기 편함만 잃는다.

    🔴 누락만 면제한다. 없던 파생 표기가 **새로 생기는 것**은 여전히 거부다
    (`8470.7` → `8471` 같은 반올림이 그 경로로 들어온다).
    """
    return {f"{m.group(1).replace(',', '')}|{m.group(2)}"
            for m in _DERIVED.finditer(body)}


def _strip_meta(text: str) -> str:
    """답변 뒤에 붙은 편집 후기를 잘라낸다.

    HCX-007은 "(수정 사항: 1. … 2. …)"를 습관적으로 붙인다. 프롬프트로 막지
    못했고, 그 안의 목록 번호가 **수치 검사까지 오염**시켜 정상 서술본이 통째로
    버려지고 있었다 (채택률 0%). 앞쪽 본문은 멀쩡하므로 잘라 쓴다.
    """
    m = _META_HEAD.search(text)
    if m:
        text = text[: m.start()]
    # 절단 지점 앞에 열린 괄호만 남는 경우가 있다
    text = text.rstrip().rstrip("([").rstrip()
    if text.count("(") > text.count(")"):
        text = text[: text.rfind("(")].rstrip()
    return text


def narrate(
    llm: LLMProvider | None,
    body: str,
    *,
    question: str,
    max_tokens: int = 8192,
) -> tuple[str, str]:
    """(최종 답변, 사유). 실패하면 원본을 그대로 돌려준다.

    사유는 `think_trace`에 남겨 무슨 일이 있었는지 보이게 한다 —
    조용히 폴백하면 LLM이 실제로 동작하는지 알 수 없다.
    """
    if llm is None or getattr(llm, "name", "") == "stub":
        return body, "stub — 템플릿 유지"
    if not body.strip():
        return body, "본문 없음 — 스킵"

    try:
        resp = llm.chat(
            [
                {"role": "system", "content": SYSTEM},
                {"role": "user",
                 "content": f"질문: {question}\n\n답변 초안:\n{body}\n\n위 답변을 다듬어라."},
            ],
            max_tokens=max_tokens,
            temperature=0.1,
        )
    except Exception as exc:                       # 네트워크·API 오류
        # 🔴 500을 내지 않는다. 결정론 답변이 이미 손에 있으므로 그것으로 답한다 —
        #    기권보다도 낫고, 재시도를 노리는 5xx보다도 낫다.
        log.warning("서술 생성 실패(%s) — 템플릿으로 강등", exc)
        return body, f"LLM 오류({type(exc).__name__}) — 템플릿 강등"

    if not resp.usable:                            # 빈 응답 · 추론 절단
        return body, "LLM 응답 불가(절단/빈응답) — 템플릿 강등"

    out = _strip_meta(resp.content.strip())
    if not out:
        return body, "본문 없이 메타 주석뿐 — 템플릿 유지"

    # 🔴 수치 동일성 — 새 숫자는 절대 불가, 누락은 파생 표기만 면제한다
    before, after = _numbers(body), _numbers(out)
    lost = before - after - _display_aliases(body)
    added = after - before
    if lost or added:
        log.warning("서술본 수치 불일치 — 템플릿 유지 (누락 %s, 추가 %s)",
                    sorted(lost)[:3], sorted(added)[:3])
        return body, f"수치 불일치(누락 {len(lost)}·추가 {len(added)}) — 템플릿 유지"

    # 인용이 사라지면 근거를 잃는다 (평가지표 2)
    if set(_CITE.findall(body)) - set(_CITE.findall(out)):
        return body, "인용 유실 — 템플릿 유지"

    # 🔴 메타 주석 혼입 — 편집 후기를 답변에 섞었다
    if _META.search(out):
        return body, "메타 주석 혼입 — 템플릿 유지"

    # 🔴 확정 사실의 추측화 — 원본에 없던 hedging이 생기면 정확성이 훼손된다.
    #    지표 1(정확성)과 3(정보한계대응)에서 동시에 감점되는 유형이다.
    if _HEDGE.search(out) and not _HEDGE.search(body):
        return body, f"추측 표현 주입({_HEDGE.search(out).group()}) — 템플릿 유지"

    usage = resp.usage or {}
    return out, f"LLM 서술 적용 (tokens={usage.get('total_tokens', '?')})"
