"""파서 4종 회귀 테스트 — 실제 코퍼스 문서 사용 (AC-TEST1, AC-TEST2)."""

from __future__ import annotations

import pytest

from conftest import make_meta, needs_corpus

pytestmark = [needs_corpus, pytest.mark.corpus]


# ── 🔴 인코딩 함정 회귀 (AC-TEST2) ─────────────────────────────────────────


def test_exchange_declares_euckr_but_is_utf8(corpus_root, manifest):
    """거래소공시 1,469건: meta는 euc-kr을 선언하지만 바이트는 UTF-8이다.

    meta를 신뢰하면 전건 문자 파괴 — 이 테스트가 회귀를 막는다.
    """
    row = next(m for m in manifest if m["doc_group"] == "exchange")
    path = corpus_root / row["file_path"] / f"{row['rcept_no']}.xml"
    raw = path.read_bytes()

    assert b"euc-kr" in raw[:600].lower(), "meta charset 선언이 사라졌다면 전제 재확인 필요"
    with pytest.raises(UnicodeDecodeError):
        raw.decode("euc-kr")
    text = raw.decode("utf-8")
    assert "<html" in text[:200].lower()


def test_periodic_is_real_xml_utf8(corpus_root, manifest):
    row = next(m for m in manifest if m["doc_group"] == "periodic")
    path = corpus_root / row["file_path"] / f"{row['rcept_no']}.xml"
    head = path.read_bytes()[:400].decode("utf-8")
    assert "<?xml" in head and "DOCUMENT" in head


# ── PeriodicParser ─────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def samsung_annual(corpus_root, manifest):
    from dart_agent.parsers.periodic import PeriodicParser

    row = next(
        (m for m in manifest if m["doc_id"] == "periodic_20250311001085"), None
    )
    if row is None:
        pytest.skip("삼성전자 2024 사업보고서 없음")
    return PeriodicParser().parse(make_meta(row), corpus_root)


def test_periodic_never_raises(corpus_root, manifest):
    """AC-P1: 파서는 예외를 던지지 않는다."""
    from dart_agent.parsers.periodic import PeriodicParser

    p = PeriodicParser()
    for row in [m for m in manifest if m["doc_group"] == "periodic"][:3]:
        res = p.parse(make_meta(row), corpus_root)
        assert res.meta.doc_id == row["doc_id"]


def test_periodic_sections_have_legal_toc_paths(samsung_annual):
    """법정 목차 주소가 부여된다 (D2 주소 지정의 전제)."""
    paths = {s.path for s in samsung_annual.sections}
    for expected in ("I", "II", "III", "II-1", "III-1"):
        assert expected in paths, f"{expected} 누락"


def test_periodic_xbrl_revenue_is_exact(samsung_annual):
    """🔴 정답 대조: 삼성전자 2024 연결 매출액 = 300,870,903백만원 (공개 사실)."""
    hits = [
        f for f in samsung_annual.fin_facts
        if f.metric_key == "revenue" and f.fy == 2024
        and f.basis == "consolidated" and f.axis is None and f.source == "xbrl"
    ]
    assert hits, "연결 매출액 XBRL fact 미추출"
    assert any(h.value_krw == 300_870_903_000_000 for h in hits)


def test_periodic_separates_consolidated_and_separate(samsung_annual):
    """연결/별도가 ACONTEXT로 분리된다 — 혼동하면 오답이 된다."""
    op = [
        f for f in samsung_annual.fin_facts
        if f.metric_key == "operating_income" and f.fy == 2024 and f.axis is None
    ]
    bases = {f.basis for f in op}
    assert {"consolidated", "separate"} <= bases


def test_periodic_period_scope_recorded(samsung_annual):
    """period_scope가 기록된다 — 누적↔당기 혼합 방지의 전제."""
    scoped = [f for f in samsung_annual.fin_facts if f.period_scope]
    assert scoped, "period_scope 미기록"
    assert all(f.period_scope for f in scoped)


def test_periodic_table_fallback_on_untagged_doc(corpus_root, manifest):
    """🔴 Stage C: XBRL 미태깅 문서(half·quarter 72%)에서 표 폴백이 동작한다."""
    from dart_agent.parsers.periodic import PeriodicParser

    p = PeriodicParser()
    for row in [
        m for m in manifest
        if m["doc_group"] == "periodic" and m["doc_subtype"] in ("half", "quarter")
        and not m["is_correction"]
    ][:25]:
        res = p.parse(make_meta(row), corpus_root)
        if not any(f.source == "xbrl" for f in res.fin_facts):
            assert any(f.source == "table" for f in res.fin_facts), (
                f"{row['doc_id']}: XBRL도 표도 없음 — Stage C 미작동"
            )
            return
    pytest.skip("표본에 미태깅 문서 없음")


# ── ExchangeParser ─────────────────────────────────────────────────────────


def test_exchange_yields_contract_event(corpus_root, manifest):
    from dart_agent.parsers.exchange import ExchangeParser

    p = ExchangeParser()
    rows = [m for m in manifest if m["doc_group"] == "exchange"][:6]
    for row in rows:
        res = p.parse(make_meta(row), corpus_root)
        assert len(res.contract_events) == 1, f"{row['doc_id']}: 계약 이벤트 1건이어야 함"


def test_exchange_korean_not_mojibake(corpus_root, manifest):
    """인코딩이 맞으면 한국어가 정상 문자로 나온다."""
    from dart_agent.parsers.exchange import ExchangeParser

    p = ExchangeParser()
    row = next(m for m in manifest if m["doc_group"] == "exchange")
    res = p.parse(make_meta(row), corpus_root)
    blob = " ".join(
        str(x) for e in res.contract_events
        for x in (e.event_kind, e.contract_kind, e.detail, e.counterparty) if x
    )
    assert blob, "추출 텍스트 없음"
    assert "�" not in blob, "치환문자 발견 — 인코딩 오류"
    assert any("가" <= ch <= "힣" for ch in blob), "한글 없음"


def test_exchange_correction_carries_original_pointer(corpus_root, manifest):
    """🔴 정정공시는 원본 포인터를 보유해야 한다 — 체인 해소의 전제."""
    from dart_agent.parsers.exchange import ExchangeParser

    p = ExchangeParser()
    found = False
    for row in [m for m in manifest if m["doc_group"] == "exchange" and m["is_correction"]][:6]:
        res = p.parse(make_meta(row), corpus_root)
        if res.corrections and any(c.target_submit_dt for c in res.corrections):
            found = True
            break
    assert found, "정정공시에서 target_submit_dt 미추출 — 체인 복원 불가"


# ── HoldingParser ──────────────────────────────────────────────────────────


def test_holding_before_after_rates(corpus_root, manifest):
    from dart_agent.parsers.holding import HoldingParser

    p = HoldingParser()
    for row in [m for m in manifest if m["doc_group"] == "holding"][:5]:
        res = p.parse(make_meta(row), corpus_root)
        assert len(res.holding_events) == 1
        assert res.holding_events[0].rate_after is not None


def test_holding_chain_pointer_present(corpus_root, manifest):
    """BFR_RPT_DT = 명시 체인 포인터 (AC-C2 — 근사 매칭 불필요 근거)."""
    from dart_agent.parsers.holding import HoldingParser

    p = HoldingParser()
    n = sum(
        1 for row in [m for m in manifest if m["doc_group"] == "holding"][:8]
        for e in HoldingParser().parse(make_meta(row), corpus_root).holding_events
        if e.prev_report_dt
    )
    assert n > 0, "prev_report_dt 전건 미추출"


# ── MajorParser ────────────────────────────────────────────────────────────


def test_major_event_kind_never_empty(corpus_root, manifest):
    """미매핑 유형도 원문으로 채우고 warning을 남긴다 (침묵 실패 금지)."""
    from dart_agent.parsers.major import MajorParser

    p = MajorParser()
    for row in [m for m in manifest if m["doc_group"] == "major"][:8]:
        res = p.parse(make_meta(row), corpus_root)
        assert len(res.capital_events) == 1
        assert res.capital_events[0].event_kind.strip()


def test_major_normalizes_known_funding_kinds(corpus_root, manifest):
    """자금조달 유형이 정규화된다 — 유형별 집계 질의의 전제."""
    from dart_agent.parsers.major import MajorParser

    p = MajorParser()
    kinds = set()
    for row in [m for m in manifest if m["doc_group"] == "major"][:60]:
        res = p.parse(make_meta(row), corpus_root)
        kinds |= {e.event_kind for e in res.capital_events}
    known = {"자기주식취득", "자기주식처분", "유상증자", "전환사채(CB)",
             "신주인수권부사채(BW)", "교환사채(EB)", "합병", "분할", "감자"}
    assert kinds & known, f"정규화 유형 0건: {sorted(kinds)[:6]}"
