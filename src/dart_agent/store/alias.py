"""기업 별칭 정규화 (SPEC §2-2).

실측 근거: 질의는 통용명("현대차 매출")으로 들어오지만 조인 키는 DART 공식 법인명("현대자동차")이다.
별칭 사전 없이는 조회가 실패한다 (proposal §2-8 P6).
"""

from __future__ import annotations

import csv
import re
import sqlite3
from pathlib import Path

# 실측 확인된 통용명 ↔ 법인명 불일치 (AC-A3). 코퍼스 README에 명시된 것 + 자주 쓰이는 축약.
MANUAL_ALIASES: dict[str, str] = {
    "현대차": "현대자동차",
    "KT": "케이티",
    "케이티": "케이티",
    "엔씨소프트": "NC",
    "엔씨": "NC",
    "LIG넥스원": "LIG디펜스앤에어로스페이스",
    "리그넥스원": "LIG디펜스앤에어로스페이스",
    "JYP Ent.": "JYP Ent",
    "JYP": "JYP Ent",
    "JYP엔터테인먼트": "JYP Ent",
    "하이닉스": "SK하이닉스",
    "포스코": "POSCO홀딩스",
    "포스코홀딩스": "POSCO홀딩스",
    "네이버": "NAVER",
    "삼성전자주식회사": "삼성전자",
    "LG엔솔": "LG에너지솔루션",
    "에스케이텔레콤": "SK텔레콤",
    "한국항공우주산업": "한국항공우주",
    "카카오톡": "카카오",
    "이마트": "이마트",
    "SM": "에스엠",
    "에스엠엔터테인먼트": "에스엠",
    "YG": "와이지엔터테인먼트",
    "와이지": "와이지엔터테인먼트",
}

_STRIP = re.compile(r"\((주|유|재)\)|주식회사|㈜|[\s\.\-_·ㆍ,]")


def normalize(alias: str) -> str:
    """별칭 정규화 키. 공백·(주)·주식회사·구두점 제거 후 소문자 (AC-A4)."""
    return _STRIP.sub("", (alias or "").strip()).lower()


class AliasConflict(RuntimeError):
    """동일 정규화 키가 서로 다른 corp_code를 가리키는 경우 — 침묵 금지 (AC-A2)."""


def load_universe(csv_path: Path) -> list[dict[str, str]]:
    """universe.csv 로드. 🔴 corp_code/stock_code는 문자열 유지 (선행 0 보존)."""
    # UTF-8 BOM 파일이므로 utf-8-sig
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        return [dict(row) for row in csv.DictReader(f)]


def build_alias_table(conn: sqlite3.Connection, universe: list[dict[str, str]]) -> dict[str, int]:
    """company + company_alias 적재. 별칭 충돌 시 AliasConflict (AC-A1, AC-A2)."""
    stats = {"companies": 0, "aliases": 0, "manual": 0, "skipped_manual": 0}
    by_corp_name: dict[str, str] = {}

    for row in universe:
        corp_code = (row.get("corp_code") or "").strip()
        corp_name = (row.get("corp_name") or "").strip()
        if not corp_code or not corp_name:
            continue
        conn.execute(
            "INSERT INTO company(corp_code,corp_name,listed_name,corp_eng_name,stock_code,"
            "market,industry,sector,listing_date,market_cap) "
            "VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(corp_code) DO NOTHING",
            (
                corp_code,
                corp_name,
                (row.get("listed_name") or "").strip() or None,
                (row.get("corp_eng_name") or "").strip() or None,
                (row.get("stock_code") or "").strip() or None,
                (row.get("market") or "").strip() or None,
                (row.get("industry") or "").strip() or None,
                (row.get("sector") or "").strip() or None,
                (row.get("listing_date") or "").strip() or None,
                _as_int(row.get("market_cap")),
            ),
        )
        stats["companies"] += 1
        by_corp_name[corp_name] = corp_code

        for kind, value in (
            ("corp_name", corp_name),
            ("listed_name", row.get("listed_name")),
            ("eng", row.get("corp_eng_name")),
            ("stock_code", row.get("stock_code")),
        ):
            if _add_alias(conn, value, corp_code, kind):
                stats["aliases"] += 1

    # 수기 별칭은 universe 적재 후 — 대상 법인명이 존재해야 연결 가능
    for alias, target_corp_name in MANUAL_ALIASES.items():
        corp_code = by_corp_name.get(target_corp_name)
        if corp_code is None:
            stats["skipped_manual"] += 1
            continue
        if _add_alias(conn, alias, corp_code, "manual"):
            stats["manual"] += 1
    conn.commit()
    return stats


def _as_int(v: str | None) -> int | None:
    try:
        return int(float(str(v).replace(",", "")))
    except (TypeError, ValueError):
        return None


def _add_alias(conn: sqlite3.Connection, raw: str | None, corp_code: str, kind: str) -> bool:
    if not raw or not str(raw).strip():
        return False
    key = normalize(str(raw))
    if not key:
        return False
    existing = conn.execute(
        "SELECT corp_code, alias_raw FROM company_alias WHERE alias_norm=?", (key,)
    ).fetchone()
    if existing is not None:
        if existing["corp_code"] != corp_code:
            raise AliasConflict(
                f"alias {raw!r} (norm={key!r}) maps to both "
                f"{existing['corp_code']} ({existing['alias_raw']}) and {corp_code}"
            )
        return False
    conn.execute(
        "INSERT INTO company_alias(alias_norm,alias_raw,corp_code,alias_kind) VALUES(?,?,?,?)",
        (key, str(raw).strip(), corp_code, kind),
    )
    return True


def resolve(conn: sqlite3.Connection, text: str) -> str | None:
    """단일 별칭 → corp_code. 미등록은 None → abstention out_of_universe."""
    row = conn.execute(
        "SELECT corp_code FROM company_alias WHERE alias_norm=?", (normalize(text),)
    ).fetchone()
    return row["corp_code"] if row else None


def find_in_text(conn: sqlite3.Connection, text: str) -> list[str]:
    """질의 문장에서 등장하는 기업들을 corp_code로 해석 (긴 별칭 우선, 중복 제거).

    정규화된 질의 문자열에 별칭 정규화 키가 부분 문자열로 포함되는지로 판정한다.
    한국어는 조사가 붙으므로("삼성전자의") 완전일치로는 잡히지 않는다.
    """
    hay = normalize(text)
    if not hay:
        return []
    rows = conn.execute(
        "SELECT alias_norm, corp_code FROM company_alias "
        "WHERE length(alias_norm) >= 2 ORDER BY length(alias_norm) DESC"
    ).fetchall()
    found: list[str] = []
    consumed = hay
    for r in rows:
        if r["alias_norm"] in consumed and r["corp_code"] not in found:
            found.append(r["corp_code"])
            # 부분 문자열 중복 매칭 방지 (예: "SK하이닉스" 매칭 후 "SK" 재매칭 차단)
            consumed = consumed.replace(r["alias_norm"], " ")
    return found


# 🔴 유니버스 **밖** 기업명을 알아보기 위한 형태 사전 (2026-09-02 신설).
#
#   문제: `mentions_company`가 "기업|회사|사|㈜" 정규식만 봤다. 그래서 기업명만 적고
#   그 단어들을 안 쓴 질의 — "LG화학의 주요 위험요인은?" — 가 **기업 미언급**으로 분류돼
#   out_of_universe가 아니라 ambiguous로 기권했다. 실측 응답이 "어느 기업의 공시를
#   확인할까요?"였다. 기업명을 명시한 사용자에게 되묻는 것이라 정보한계 대응에서 감점된다.
#   (Q-07 "존재하지않는**회사**"가 통과했던 건 우연히 `사`가 들어갔기 때문이다.)
#
#   설계: **정밀도 우선**. 놓치면 종전 동작(ambiguous)으로 떨어질 뿐이고, 잘못 잡으면
#   기업이 없는 질의에 "해당 기업을 확인할 수 없습니다"가 나간다. 그래서 일반명사와
#   겹치는 접미사(산업·공업·전기·에너지·통신·시스템·솔루션…)는 **의도적으로 뺐다**.
#   ⚠️ 기존 정규식이 `사` 한 글자를 잡고 있어 원래도 매우 느슨했다 — 아래는 그보다
#      훨씬 좁으므로 새로 생기는 오탐은 실질적으로 없다.
_CORP_SUFFIXES = (
    "홀딩스", "지주", "화학", "케미칼", "제약", "바이오로직스", "바이오사이언스",
    "중공업", "조선해양", "에너빌리티", "이노베이션", "머티리얼즈", "일렉트릭",
    "엔터테인먼트", "텔레콤", "증권", "카드", "캐피탈", "손해보험", "생명보험",
    "백화점", "면세점", "제철", "제강", "정유", "로보틱스", "디스플레이",
    "네트웍스", "대한통운", "건설", "물산", "오션", "에어로스페이스", "이앤씨",
    "헬스케어",
)

# ① 영문 약어 + 한글 (LG화학 · SK이노베이션 · GS리테일) — 거의 기업 전용
# ② 한글/영문 2자+ + 위 접미사 (한화오션 · 두산에너빌리티 · 삼성바이오로직스)
_COMPANY_SHAPE = re.compile(
    r"[A-Z]{2,}[가-힣]{2,}"
    r"|[가-힣A-Za-z]{2,}(?:" + "|".join(_CORP_SUFFIXES) + r")"
)
# 매치 뒤에는 (조사 하나) + 공백·문장부호·문장끝이 와야 한다 — 단어 중간 우연 매칭 차단.
# 🔴 조사를 선택적으로 허용해야 한다. ②(한글 접미사) 분기는 조사를 소비하지 않으므로
#    경계만 요구하면 "한화오션**의**"에서 곧바로 탈락해 분기 전체가 죽는다.
_TAIL_OK = re.compile(
    r"(?:에서|으로|에게|까지|부터|이나|[은는이가의를을과와도만에로])?"
    r"(?:[\s,\.\?!)\]]|$)"
)

# ①은 한글을 탐욕적으로 먹으므로 조사가 딸려온다("LG화학의"). 긴 것부터 떼어낸다.
_TRAILING_PARTICLES = (
    "이라는", "이라고", "에서는", "으로는", "에게는",
    "에서", "으로", "에게", "까지", "부터", "이나", "이란", "이라",
    "와의", "과의", "에는", "에도", "이며", "이고", "이야",
    "의", "은", "는", "이", "가", "를", "을", "과", "와", "도", "만", "에", "로",
)
_MIN_HANGUL = 2


def _strip_particle(token: str) -> str:
    for p in _TRAILING_PARTICLES:
        if token.endswith(p) and len(token) - len(p) >= 2:
            return token[: -len(p)]
    return token


def detect_company_mention(text: str) -> str | None:
    """질의에서 **기업명처럼 생긴** 토큰을 찾는다. 없으면 None.

    유니버스 별칭 해석(`find_in_text`)이 실패한 뒤에만 의미가 있다 —
    보유 기업이면 그쪽에서 이미 잡히기 때문이다. 여기서 잡히면
    "모호함(ambiguous)"이 아니라 "보유하지 않은 기업(out_of_universe)"이다.
    """
    for m in _COMPANY_SHAPE.finditer(text or ""):
        if not (_TAIL_OK.match(text, m.end()) or m.end() == len(text)):
            continue
        tok = _strip_particle(m.group(0))
        # 🔴 조사를 떼고 나면 한글이 거의 남지 않는 것은 기업명이 아니다.
        #    이게 없으면 "DART에서 조회 가능한가?"가 기업 'DART에서'로 잡힌다.
        if sum(1 for ch in tok if "가" <= ch <= "힣") < _MIN_HANGUL:
            continue
        return tok
    return None


def suggest_similar(
    conn: sqlite3.Connection, candidate: str, limit: int = 5
) -> list[str]:
    """유니버스 밖 기업명과 **앞부분을 공유하는** 보유 기업을 제안한다.

    "LG화학" → LG에너지솔루션·LG생활건강·… 처럼 계열 접두를 공유하는 것을 찾는다.
    거부하고 끝내지 않고 대안을 주기 위한 것이다 (AC-AB2).
    """
    if not candidate:
        return []
    m = re.match(r"[A-Za-z]{2,}|[가-힣]{2}", candidate)
    if not m:
        return []
    prefix = m.group(0)
    rows = conn.execute(
        "SELECT corp_name FROM company WHERE corp_name LIKE ? "
        "ORDER BY market_cap DESC NULLS LAST LIMIT ?",
        (f"{prefix}%", limit),
    ).fetchall()
    return [r["corp_name"] for r in rows]


def by_sector(conn: sqlite3.Connection, sector: str) -> list[str]:
    """섹터명 → corp_code 목록. "2차전지 기업 A와 B" 류 질의 해석용 (SPEC §3-3)."""
    rows = conn.execute(
        "SELECT corp_code FROM company WHERE sector=? ORDER BY market_cap DESC", (sector,)
    ).fetchall()
    return [r["corp_code"] for r in rows]


def all_sectors(conn: sqlite3.Connection) -> list[str]:
    return [
        r["sector"]
        for r in conn.execute(
            "SELECT DISTINCT sector FROM company WHERE sector IS NOT NULL ORDER BY sector"
        )
    ]
