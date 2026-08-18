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
