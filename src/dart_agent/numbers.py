"""숫자·단위 정규화 (SPEC §1-4, Stage D).

실측 근거 (proposal/00-research-findings.md §2-6-bis):
  - 금액 단위가 문서마다 원/천원/백만원 혼용
  - 60건 표본 중 27%는 인접 범위에 단위 표기 없음
  - 음수는 괄호 표기: "(11,526,297)" → -11526297  [삼성전자 별도 영업이익 실측]

정규화 없이 기업 간 비교를 수행하면 1,000배 오차가 발생한다 (R5b).
"""

from __future__ import annotations

import re

UNIT_HIGH = "high"
UNIT_LOW = "low"

# 단위 → 배율. 긴 표기가 먼저 와야 한다 ("백만원"이 "만원"보다 먼저).
UNIT_SCALES: dict[str, int] = {
    "십억원": 1_000_000_000,
    "억원": 100_000_000,
    "백만원": 1_000_000,
    "천만원": 10_000_000,
    "만원": 10_000,
    "천원": 1_000,
    "원": 1,
}

# "단위 : 백만원" / "(단위:천원)" / "단위 ： 원" — 공백·전각콜론 허용
_UNIT_DECL = re.compile(r"단\s*위\s*[:：]?\s*[(（]?\s*([가-힣]{0,3}\s*원)")

# 숫자 토큰: 콤마 포함, 소수점 허용, 선행 부호 허용
_NUM = re.compile(r"^[\s]*\(?\s*([+-]?[\d,]+(?:\.\d+)?)\s*\)?[\s]*$")

# 결측 표기 — DART 표에서 실측되는 형태
_MISSING = {"", "-", "－", "—", "–", "N/A", "n/a", "해당사항없음", "해당사항 없음", ".", "·"}


def detect_unit(text: str) -> str | None:
    """텍스트에서 단위 선언을 찾아 정규화된 단위 문자열을 반환한다.

    미검출 시 None. 호출자는 unit_confidence='low'로 처리해야 한다 (AC-U1).
    """
    if not text:
        return None
    m = _UNIT_DECL.search(text)
    if not m:
        return None
    raw = re.sub(r"\s+", "", m.group(1))
    return raw if raw in UNIT_SCALES else None


def scale_of(unit: str | None) -> int | None:
    """단위 문자열 → 배율. 미지원 단위는 None."""
    if unit is None:
        return None
    return UNIT_SCALES.get(re.sub(r"\s+", "", unit))


def clean_number(raw: str | None) -> float | None:
    """DART 표 셀 문자열 → 숫자.

    - 콤마 제거
    - 괄호는 음수 (회계 관행)
    - 결측 표기는 None
    """
    if raw is None:
        return None
    s = raw.strip()
    if s in _MISSING:
        return None
    # 괄호 음수 판정은 괄호 제거 *전에* 해야 한다
    negative = s.startswith("(") and s.endswith(")")
    m = _NUM.match(s)
    if not m:
        return None
    body = m.group(1).replace(",", "")
    if body in {"", "+", "-"}:
        return None
    try:
        val = float(body)
    except ValueError:
        return None
    if negative:
        val = -abs(val)
    return val


def to_krw(raw: str | None, unit: str | None) -> tuple[int | None, str]:
    """(원 단위 정수, unit_confidence) 반환.

    단위가 없으면 값을 만들지 않는다 — 추측 금지 (AC-U1).
    호출자가 XBRL 교차검증으로 배율을 확정한 경우에만 unit을 넘겨야 한다.
    """
    num = clean_number(raw)
    if num is None:
        return None, UNIT_LOW
    scale = scale_of(unit)
    if scale is None:
        return None, UNIT_LOW
    return int(round(num * scale)), UNIT_HIGH


def infer_scale_by_magnitude(
    raw: str, reference_krw: int, tolerance: float = 0.02
) -> str | None:
    """단위 미표기 값의 배율을 기준값(원 단위)과 자릿수 대조로 역추정한다 (AC-U1 3-a).

    XBRL로 확보한 동일 지표 값(reference_krw)이 있을 때만 사용한다.
    tolerance 내에서 일치하는 배율이 유일할 때만 반환하고, 애매하면 None.
    """
    num = clean_number(raw)
    if num is None or num == 0 or reference_krw == 0:
        return None
    matches = [
        unit
        for unit, scale in UNIT_SCALES.items()
        if abs(num * scale - reference_krw) <= abs(reference_krw) * tolerance
    ]
    # 배율이 서로 다른 후보가 여럿이면 판정 불가
    distinct = {UNIT_SCALES[u] for u in matches}
    if len(distinct) != 1:
        return None
    return matches[0]


def josa(word: str, pair: str = "이/가") -> str:
    """한국어 조사 자동 선택. 받침 유무로 결정한다.

    "삼성전자" + "이/가" → "가"  ·  "SK하이닉스" + "이/가" → "가"(스=받침없음)
    영문·숫자 끝은 발음 기준 근사 — 완벽하지 않으나 "삼성전자이" 같은 명백한 오류를 막는다.
    """
    with_batchim, without = pair.split("/")
    if not word:
        return without
    ch = word.rstrip()[-1]
    if "가" <= ch <= "힣":
        return with_batchim if (ord(ch) - 0xAC00) % 28 else without
    # 숫자는 한국어 읽기가 확정적이다: 영(0)·일·삼·육·칠·팔이 받침
    if ch in "0136 78".replace(" ", ""):
        return with_batchim
    if ch.isdigit():
        return without
    # 🔴 영문은 읽기가 불확정하다 — "NAVER"는 네이버(무받침), "NC"는 엔씨(무받침).
    #    글자 단위로 읽는 경우(L=엘, M=엠)와 단어로 읽는 경우가 섞이므로
    #    오답이 덜 어색한 무받침을 기본값으로 둔다.
    return without


def fmt_krw(value_krw: int | None) -> str:
    """사람이 읽는 한국어 금액 표기. 답변 문장 생성용."""
    if value_krw is None:
        return "확인 불가"
    neg = value_krw < 0
    v = abs(value_krw)
    if v >= 1_000_000_000_000:
        s = f"{v / 1_000_000_000_000:,.1f}조원"
    elif v >= 100_000_000:
        s = f"{v / 100_000_000:,.1f}억원"
    elif v >= 10_000:
        s = f"{v / 10_000:,.0f}만원"
    else:
        s = f"{v:,}원"
    return ("-" if neg else "") + s
