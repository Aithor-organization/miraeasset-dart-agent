"""한국어 형태소 토크나이저 (SPEC §3-1 AC-R1, AC-R2).

🔴 자체 개발이 필요한 유일한 코어 요소 — AITHOR·AI-research-SKILLs 양쪽에
   MeCab/Kiwipiepy/KoNLPy/Nori 언급 0건 (proposal §4-2 실측).
   AITHOR `rag.py:_tokens`는 정규식이라 한국어에서 조사가 붙어 BM25가 열화된다.

kiwipiepy 미설치 시 문자 2-3gram으로 자동 강등한다 (가용성 우선, AC-R1).
"""

from __future__ import annotations

import re

# 색인 대상 품사: 일반명사·고유명사·외국어·숫자·영문
_KEEP_TAGS = {"NNG", "NNP", "SL", "SN", "SH"}

# 금융 도메인 사용자 사전 (AC-R2) — 형태소 분석기가 쪼개면 안 되는 복합어
FINANCE_TERMS: tuple[str, ...] = (
    "연결기준", "별도기준", "유상증자", "무상증자", "전환사채", "신주인수권부사채",
    "교환사채", "조건부자본증권", "자기주식", "자기주식취득", "자기주식처분",
    "대량보유", "대량보유상황보고서", "단일판매공급계약", "공급계약", "설비투자",
    "신규시설투자", "증감률", "영업이익", "당기순이익", "매출액", "자산총계",
    "부채총계", "자본총계", "요약재무정보", "연결재무제표", "재무상태표",
    "손익계산서", "현금흐름표", "자본변동표", "특수관계자", "종속회사", "계열회사",
    "최대주주", "기재정정", "주요사항보고서", "사업보고서", "반기보고서", "분기보고서",
)

_WORD = re.compile(r"[가-힣]+|[A-Za-z]+|\d+")


class Tokenizer:
    """형태소 토크나이저. `.mode`로 실제 사용 경로를 노출한다 (침묵 강등 금지)."""

    def __init__(self, extra_terms: tuple[str, ...] = ()) -> None:
        self.mode = "ngram"
        self._kiwi = None
        try:
            from kiwipiepy import Kiwi  # type: ignore

            kiwi = Kiwi()
            for term in FINANCE_TERMS + tuple(extra_terms):
                try:
                    kiwi.add_user_word(term, "NNP")
                except Exception:  # 중복 등록 등은 무시
                    pass
            self._kiwi = kiwi
            self.mode = "kiwi"
        except Exception:
            self._kiwi = None
            self.mode = "ngram"

    def tokens(self, text: str) -> list[str]:
        if not text:
            return []
        if self._kiwi is not None:
            return self._kiwi_tokens(text)
        return self._ngram_tokens(text)

    def _kiwi_tokens(self, text: str) -> list[str]:
        out: list[str] = []
        # 긴 텍스트는 kiwi가 느리므로 상한을 둔다 (섹션 본문은 수만자 가능)
        for tok in self._kiwi.tokenize(text[:200_000]):
            if tok.tag in _KEEP_TAGS and len(tok.form) >= 1:
                out.append(tok.form.lower())
        return out

    @staticmethod
    def _ngram_tokens(text: str) -> list[str]:
        """폴백: 어절 + 문자 2·3gram. 조사 포함이라 품질은 낮지만 동작은 한다."""
        words = _WORD.findall(text[:200_000])
        out: list[str] = []
        for w in words:
            wl = w.lower()
            out.append(wl)
            if len(wl) >= 3:
                out.extend(wl[i:i + 2] for i in range(len(wl) - 1))
                out.extend(wl[i:i + 3] for i in range(len(wl) - 2))
        return out


_DEFAULT: Tokenizer | None = None


def default_tokenizer(extra_terms: tuple[str, ...] = ()) -> Tokenizer:
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = Tokenizer(extra_terms)
    return _DEFAULT
