"""HCX 목차 라우팅 — 규칙이 못 찾은 질문의 섹션 주소를 LLM이 고른다.

🔴 **왜 LLM에게 이걸 맡기는가 (수치는 안 맡기면서)**

D1(수치 결정론)이 지키는 것은 **값**이지 **어디를 볼지**가 아니다. 목차 주소
선택은 semantic matching 문제라 정규식이 구조적으로 약하다. 실측이 그렇다:

  "메리츠금융지주의 **배당**에 관한 사항은?"  → INTENT_PATHS에 배당 규칙이 **없음**
  "효성중공업의 **사업의 개요**를 알려줘"      → 패턴이 `사업\\s*개요`라 가운데 "의"에 막힘

둘 다 주소를 못 찾아 전문검색으로 흘렀고 엉뚱한 섹션을 인용했다 (골드셋 실패
7건 중 **4건**이 이 유형). 규칙을 더 붙이면 두더지잡기가 된다 — 다음 질문은
"현금배당성향은?"이고 그 다음은 "주주환원 정책"이다.

🔴 **그럼에도 LLM을 믿지는 않는다 — 화이트리스트로 가둔다**

LLM이 반환한 주소는 **DB에 실재하는 법정 목차**와 대조해 통과한 것만 쓴다.
없는 주소를 지어내면 그 항목만 버린다. 조회는 여전히 SQL이므로, 최악의 경우도
"엉뚱한 섹션을 보여준다"이지 "없는 사실을 만든다"가 아니다.

**규칙이 주소를 찾았으면 호출하지 않는다** — 규칙이 맞을 때 LLM을 부르면
비용과 429 위험만 늘고 정확도는 그대로다 (실측: 규칙 매칭 시 section 21/21).
"""

from __future__ import annotations

import logging
import re

from ..llm.provider import LLMProvider

log = logging.getLogger(__name__)

# 법정 목차 — 기업 무관 동일하다 (DB 실측: 대부분 n=1051 = 전 문서 보유).
# 🔴 여기 없는 주소는 LLM이 뭐라 하든 버린다.
CATALOG: tuple[tuple[str, str], ...] = (
    ("I-1", "회사의 개요"),
    ("I-2", "회사의 연혁"),
    ("I-3", "자본금 변동사항"),
    ("I-4", "주식의 총수 등"),
    ("I-5", "정관에 관한 사항"),
    ("II-1", "사업의 개요"),
    ("II-2", "주요 제품 및 서비스 / 영업의 현황"),
    ("II-3", "원재료 및 생산설비"),
    ("II-4", "매출 및 수주상황"),
    ("II-5", "위험관리 및 파생거래"),
    ("II-6", "주요계약 및 연구개발활동"),
    ("II-7", "기타 참고사항"),
    ("III-1", "요약재무정보"),
    ("III-2", "연결재무제표 (재무상태표·손익계산서·현금흐름표)"),
    ("III-3", "연결재무제표 주석"),
    ("III-4", "재무제표 (별도)"),
    ("III-5", "재무제표 주석 (별도)"),
    ("III-6", "배당에 관한 사항"),
    ("III-7", "증권의 발행을 통한 자금조달에 관한 사항"),
    ("IV", "이사의 경영진단 및 분석의견"),
    ("V-1", "외부감사에 관한 사항"),
    ("V-2", "내부통제에 관한 사항"),
    ("VI-1", "이사회에 관한 사항"),
    ("VI-2", "감사제도에 관한 사항"),
    ("VI-3", "주주총회 등에 관한 사항"),
    ("VII", "주주에 관한 사항 (최대주주·지분구조)"),
    ("VIII-1", "임원 및 직원 등의 현황"),
    ("VIII-2", "임원의 보수 등"),
    ("IX", "계열회사 등에 관한 사항"),
    ("X", "대주주 등과의 거래내용"),
    ("XI-1", "공시내용 진행 및 변경사항"),
    ("XI-2", "우발부채 등에 관한 사항"),
    ("XI-3", "제재 등과 관련된 사항"),
    ("XI-4", "작성기준일 이후 발생한 주요사항 등 기타사항"),
    ("XII-3", "타법인출자 현황"),
)

_VALID = {p for p, _ in CATALOG}
_PATH = re.compile(r"\b((?:I{1,3}|IV|V|VI{0,3}|IX|XI{0,2})(?:-\d+)*)\b")

_MENU = "\n".join(f"  {p} = {t}" for p, t in CATALOG)

SYSTEM = f"""너는 한국 정기공시(사업보고서·분기보고서)의 법정 목차 안내자다.
사용자 질문이 어느 목차 항목을 묻는지 고른다.

법정 목차:
{_MENU}

규칙:
1. 위 목록에 **있는 주소만** 답한다. 없는 주소를 만들지 마라.
2. 가장 알맞은 것 1개, 애매하면 최대 2개까지.
3. 해당하는 항목이 없으면 `NONE` 이라고만 답한다.
4. 설명 없이 **주소만** 쉼표로 구분해 출력한다.

예:
질문: 배당에 관한 사항은?     → III-6
질문: 최대주주가 누구인가?    → VII
질문: 2024년 매출액은?        → NONE
"""


def route(llm: LLMProvider | None, question: str, *, max_tokens: int = 2048
          ) -> tuple[list[str], str]:
    """(섹션 주소들, 사유). 실패하면 빈 리스트 — 호출자는 기존 검색으로 간다."""
    if llm is None or getattr(llm, "name", "") == "stub":
        return [], "stub — 목차 라우팅 생략"
    try:
        resp = llm.chat(
            [{"role": "system", "content": SYSTEM},
             {"role": "user", "content": f"질문: {question}"}],
            max_tokens=max_tokens,
            temperature=0.0,
        )
    except Exception as exc:
        log.warning("목차 라우팅 실패(%s) — 검색으로 진행", exc)
        return [], f"LLM 오류({type(exc).__name__}) — 검색 진행"

    if not resp.usable:
        return [], "LLM 응답 불가 — 검색 진행"

    out = resp.content.strip()
    if "NONE" in out.upper():
        return [], "해당 목차 없음 — 검색 진행"

    # 🔴 화이트리스트 — 지어낸 주소는 버린다
    found, dropped = [], []
    for m in _PATH.finditer(out):
        p = m.group(1)
        (found if p in _VALID and p not in found else dropped).append(p)
    if not found:
        return [], f"유효 주소 없음({out[:30]}) — 검색 진행"
    if dropped:
        log.info("목차 라우팅 — 카탈로그 밖 주소 %s 폐기", sorted(set(dropped))[:3])
    return found[:2], f"LLM 목차 라우팅 → {','.join(found[:2])}"
