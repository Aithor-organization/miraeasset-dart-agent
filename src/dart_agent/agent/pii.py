"""개인정보 마스킹 (SPEC §4-4, 평가지표 6 안전성).

🔴 실측 근거: 정기공시 `VIII. 임원 및 직원 등에 관한 사항` 섹션과 `<TU AUNIT="PSN_BIH">`
   (생년월일) · `AUNIT="PSN_SEX"` (성별) 셀에 임원 개인정보가 실제로 존재한다
   (proposal §2-5). 공시가 공개 문서라 해도, 질의에 응답하며 개인 식별정보를
   그대로 재출력하는 것은 평가지표 6이 명시한 "개인정보 노출"에 해당한다.

정책: 회사 정보는 그대로, **개인 식별정보만** 마스킹한다.
"""

from __future__ import annotations

import re

# 생년월일: 1965년 03월 / 1965.03 / 65년 03월생 / 1965-03-21
_BIRTH = re.compile(
    r"(19|20)\d{2}\s*[.\-년]\s*\d{1,2}\s*[.\-월]?(\s*\d{1,2}\s*일?)?\s*생?"
)
# 주민등록번호 형태 (공시에 없어야 하지만 방어)
_RRN = re.compile(r"\b\d{6}\s*[-–]\s*\d{7}\b")
# 이메일 · 전화
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_PHONE = re.compile(r"\b0\d{1,2}[-.\s]?\d{3,4}[-.\s]?\d{4}\b")

# 개인정보가 집중된 섹션 (path 접두)
PII_SECTION_PREFIXES = ("VIII",)
PII_SECTION_TITLES = re.compile(r"임원\s*및\s*직원|임원\s*현황|직원\s*현황|임원의\s*보수|개인별\s*보수")

# 외부 모델에 전송하면 안 되는 자격증명 형태. 일반적인 "API 키 정책" 문의는
# 차단하지 않고, 값이 실제로 포함된 경우만 잡는다.
_SECRET_VALUE = re.compile(
    r"(?ix)"
    r"(?:\bsk-[a-z0-9_-]{20,}\b|\bAKIA[0-9A-Z]{16}\b|"
    r"\b(?:api[_ -]?key|secret|password|access[_ -]?token)\s*[:=]\s*[^\s]{8,}|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----)"
)

# 개인정보를 직접 요구하는 질의
PII_REQUEST = re.compile(
    r"생년월일|생일|나이|출생|주민\s*등록|주민\s*번호|성별|"
    r"개인\s*정보|연락처|이메일|전화\s*번호|주소지|"
    r"(임원|대표|사장|이사|직원)[^.\n]{0,10}(개인|신상|프로필|인적\s*사항)"
)


def is_pii_request(question: str) -> bool:
    """개인정보를 직접 요구하는 질의인지 판정."""
    return bool(PII_REQUEST.search(question or ""))


def is_sensitive_request(question: str) -> bool:
    """PII 또는 실제 자격증명 값을 포함해 외부 전송하면 안 되는 입력인지 판정."""
    return is_pii_request(question) or bool(_SECRET_VALUE.search(question or ""))


def is_pii_section(path: str | None, title: str | None) -> bool:
    if path and any(path.startswith(p) for p in PII_SECTION_PREFIXES):
        return True
    return bool(title and PII_SECTION_TITLES.search(title))


def mask_always(text: str) -> str:
    """🔴 **어느 섹션이든** 지워야 하는 식별자만 마스킹한다.

    `mask()`는 `_BIRTH`가 회계기간("2024년 12월")과 형태가 겹쳐 전역 적용이
    금지돼 있다. 그런데 주민번호·이메일·전화는 그런 충돌이 없다 — 전역으로
    막지 못할 이유가 없었는데 막지 않고 있었다.

    실측 결함 (AITHOR `security-engineer` 지적): 답변(`answer`)은 마스킹되는데
    **`retrieved_context`는 평문**이었다. "삼성전자 임원 및 직원 현황"처럼
    PII 질의 정규식에 안 걸리는 문장으로 VIII-1 섹션을 부르면, 답변에는 안
    나와도 근거 본문 1,200자에 그대로 실려 나간다.
    """
    if not text:
        return text
    out = _RRN.sub("******-*******", text)
    out = _EMAIL.sub("***@***", out)
    return _PHONE.sub("***-****-****", out)


def mask(text: str) -> str:
    """텍스트에서 개인 식별정보를 마스킹한다. 회사 수치·연도 표기는 보존.

    ⚠️ `_BIRTH`가 회계 기간 표기("2024년 12월")와 형태가 겹친다.
       따라서 **PII 섹션/질의로 한정해 호출**해야 한다 (전역 적용 금지).
    """
    if not text:
        return text
    out = _RRN.sub("******-*******", text)
    out = _EMAIL.sub("***@***", out)
    out = _PHONE.sub("***-****-****", out)
    out = _BIRTH.sub("****년 **월", out)
    return out


REFUSAL = (
    "개인 식별정보 또는 자격증명은 처리·전송하지 않습니다. "
    "공시에 포함된 정보라도 개인정보에 해당하는 항목은 응답에서 제외합니다.\n\n"
    "회사 단위 정보(임원 수, 직원 수, 보수 총액, 사업 내용 등)는 답변 가능합니다."
)
