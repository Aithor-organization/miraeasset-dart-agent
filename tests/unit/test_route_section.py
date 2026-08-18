"""HCX 목차 라우팅 회귀 가드 (2026-08-18).

🔴 이 계층은 **LLM에게 판단을 맡기되 결과를 가둔다**. 가둔 곳이 뚫리면
   존재하지 않는 섹션 주소로 조회가 나가고, 빈 결과가 "근거 없음"처럼 보인다.

배경: 골드셋 실패 7건 중 4건이 목차 주소를 못 찾아 생긴 것이었다.
  · "배당에 관한 사항"  → INTENT_PATHS에 배당 규칙이 아예 없었다
  · "사업**의** 개요"   → 패턴 `사업\\s*개요`가 가운데 조사에 막혔다
"""

import pytest

from dart_agent.agent.route_section import CATALOG, route


class FakeLLM:
    name = "fake"

    def __init__(self, out, *, raise_exc=None, usable=True):
        self._out, self._exc, self._usable = out, raise_exc, usable

    def chat(self, messages, **kw):
        if self._exc:
            raise self._exc
        return _Resp(self._out, self._usable)


class _Resp:
    def __init__(self, content, usable):
        self.content, self._usable = content, usable
        self.tool_calls, self.usage, self.truncated = [], {}, not usable

    @property
    def usable(self):
        return self._usable


def _run(out, q="배당에 관한 사항은?", **kw):
    return route(FakeLLM(out, **kw), q)


class TestCatalogIntegrity:
    def test_dividend_address_present(self):
        """🔴 실패 4건 중 3건의 정답 — DB 실측 `III-6 = 6. 배당에 관한 사항` (900건)."""
        assert ("III-6", "배당에 관한 사항") in CATALOG

    def test_business_overview_present(self):
        assert any(p == "II-1" for p, _ in CATALOG)

    def test_no_duplicate_paths(self):
        paths = [p for p, _ in CATALOG]
        assert len(paths) == len(set(paths))


class TestWhitelist:
    def test_valid_path_accepted(self):
        paths, why = _run("III-6")
        assert paths == ["III-6"] and "III-6" in why

    def test_two_paths_accepted(self):
        paths, _ = _run("II-1, II-2")
        assert paths == ["II-1", "II-2"]

    def test_caps_at_two(self):
        """주소를 많이 주면 조회가 흐려진다 — 상위 2개만."""
        paths, _ = _run("II-1, II-2, II-3, II-4")
        assert len(paths) == 2

    def test_invented_path_dropped(self):
        """🔴 카탈로그에 없는 주소는 버린다 — LLM이 지어낼 수 있다."""
        paths, why = _run("III-99")
        assert paths == [] and "유효 주소 없음" in why

    def test_partially_invented_keeps_valid_only(self):
        paths, _ = _run("III-6, XV-7")
        assert paths == ["III-6"]

    def test_none_response(self):
        paths, why = _run("NONE")
        assert paths == [] and "해당 목차 없음" in why

    def test_prose_around_path_still_parsed(self):
        """설명을 붙이지 말라고 했지만 붙일 수 있다 — 주소만 뽑아낸다."""
        paths, _ = _run("배당은 III-6 항목입니다.")
        assert paths == ["III-6"]

    def test_duplicate_path_collapsed(self):
        paths, _ = _run("III-6, III-6")
        assert paths == ["III-6"]


class TestDegradation:
    """🔴 라우팅이 실패해도 답변은 나가야 한다 — 검색 경로가 남아 있다."""

    def test_stub_skips(self):
        class Stub:
            name = "stub"
        paths, why = route(Stub(), "배당?")
        assert paths == [] and "stub" in why

    def test_none_llm(self):
        assert route(None, "배당?")[0] == []

    def test_api_error_falls_back(self):
        paths, why = _run("", raise_exc=RuntimeError("429"))
        assert paths == [] and "오류" in why

    def test_truncated_falls_back(self):
        paths, why = _run("", usable=False)
        assert paths == [] and "응답 불가" in why


class TestRuleFirstPolicy:
    """🔴 아는 것은 규칙으로 내린다 — LLM 라우팅은 롱테일 전용이다.

    부하 실측(2026-08-18, 동시 50): 429가 324회 나고 **재시도 소진 52건**이
    발생했다. 그때 LLM 라우팅이 죽으면 검색으로 떨어져 section 실패가 되살아난다.
    아래 두 케이스는 원래 LLM만 잡던 것을 **규칙으로 내려** 이 위험을 제거한 것이다.
    """

    def test_rules_still_cover_original_cases(self):
        from dart_agent.retrieval.section_map import paths_for

        assert paths_for("최대주주 지분 구조는?"), "규칙이 커버하던 것까지 잃으면 안 된다"

    @pytest.mark.parametrize("q,want", [
        ("메리츠금융지주의 배당에 관한 사항은?", "III-6"),
        ("하나금융지주의 배당에 관한 사항은?", "III-6"),
        ("삼성SDI의 현금배당 성향은?", "III-6"),
        ("효성중공업의 사업의 개요를 알려줘", "II-1"),
        ("사업 개요 알려줘", "II-1"),          # 조사 없는 원래 형태도 유지
    ])
    def test_known_gaps_now_covered_by_rules(self, q, want):
        """LLM 없이도 잡혀야 한다 — 429로 라우팅이 죽어도 정답이 나온다."""
        from dart_agent.retrieval.section_map import paths_for

        assert want in paths_for(q), f"{q!r} → {paths_for(q)} (기대 {want} 포함)"

    def test_dividend_does_not_hijack_financial_questions(self):
        """🔴 과잉 매칭 방지 — "배당금 지급 현금흐름"은 재무제표 질문이다.

        first-match 누적이라 배당 규칙이 앞선 규칙을 가로채지 않는지 본다.
        """
        from dart_agent.retrieval.section_map import paths_for

        assert "III-2-5" in paths_for("연결 현금흐름표 보여줘")
