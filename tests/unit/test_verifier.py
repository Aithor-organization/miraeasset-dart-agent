"""검증기 V1~V5 + Abstention 테스트 (AC-TEST5).

🔴 이 테스트가 환각 차단의 회귀 가드다. 여기가 깨지면 근거 없는 수치가 통과한다.
"""

import pytest

from dart_agent.agent.abstention import decide, detect_prediction, detect_unsupported
from dart_agent.agent.verifier import (extract_numbers, strip_failing_sentences, verify)

CTX = "[C1] 삼성전자 사업보고서(2024.12) · III-2-2 연결 손익계산서\n      매출액 300,870,903 (단위: 백만원) 매출액대비 2.32"
CITES = {"C1", "C2"}


class TestV1Numeric:
    def test_grounded_number_passes(self):
        rep = verify("매출액은 300,870,903백만원입니다. [C1]",
                     context=CTX, citation_ids=CITES, requirements=[])
        assert rep.ok, rep.summary()

    def test_hallucinated_number_blocked(self):
        rep = verify("매출액은 999,111,222백만원입니다. [C1]",
                     context=CTX, citation_ids=CITES, requirements=[])
        assert not rep.ok
        assert rep.v1_ungrounded_numbers

    def test_derived_display_value_accepted_when_supplied(self):
        """fmt_krw 파생 표기를 grounded로 넘기면 통과해야 한다 (실측 결함 회귀)."""
        rep = verify("매출액은 300.9조원입니다. [C1]", context=CTX, citation_ids=CITES,
                     requirements=[], grounded_values={"300.9조원"})
        assert rep.ok, rep.summary()

    def test_derived_value_without_grounding_blocked(self):
        rep = verify("매출액은 412.7조원입니다. [C1]", context=CTX, citation_ids=CITES,
                     requirements=[])
        assert not rep.ok


class TestExemptions:
    def test_year_quarter_date_exempt(self):
        nums = extract_numbers(
            "2025년 제57기 3분기 2026-03-31 2026.03 2024년 11월 18일 20260331 [C1] III-2-2"
        )
        assert nums == [], nums

    def test_ratio_and_count_kept(self):
        nums = extract_numbers("매출액대비 2.32% 12.86배 1,783,406주")
        assert len(nums) == 3


class TestV2Citations:
    def test_missing_citation_blocked(self):
        rep = verify("매출액은 300,870,903백만원입니다. [C7]",
                     context=CTX, citation_ids=CITES, requirements=[])
        assert not rep.ok
        assert "C7" in rep.v2_missing_citations


class TestV3Requirements:
    def test_met(self):
        rep = verify("삼성전자의 2024년 매출액은 300,870,903백만원입니다. [C1]",
                     context=CTX, citation_ids=CITES, requirements=["2024년 매출액"])
        assert not rep.v3_unmet_requirements, rep.summary()

    def test_unmet(self):
        rep = verify("삼성전자는 반도체 회사입니다. [C1]",
                     context=CTX, citation_ids=CITES, requirements=["2024년 영업이익"])
        assert rep.v3_unmet_requirements


class TestV4Forbidden:
    def test_blocks_target_price(self):
        rep = verify("목표주가는 10만원입니다.", context=CTX, citation_ids=set(), requirements=[])
        assert rep.v4_forbidden

    def test_blocks_prediction(self):
        rep = verify("주가가 상승할 것으로 전망됩니다.", context=CTX,
                     citation_ids=set(), requirements=[])
        assert rep.v4_forbidden

    def test_blocks_buy_recommendation(self):
        rep = verify("매수를 추천합니다.", context=CTX, citation_ids=set(), requirements=[])
        assert rep.v4_forbidden

    def test_allows_factual_statement(self):
        rep = verify("매출액이 증가했습니다. [C1]", context=CTX,
                     citation_ids=CITES, requirements=[])
        assert not rep.v4_forbidden, rep.summary()

    def test_blocks_question_echoed_unsupported_assessment(self):
        """질문에 있던 '개선' 표현은 답변 근거가 아니다."""
        answer = "매출액은 300,870,903백만원입니다. [C1] 실적이 개선되고 수익성이 회복되었습니다."
        rep = verify(answer, context=CTX, citation_ids=CITES, requirements=[])
        assert not rep.ok
        cleaned = strip_failing_sentences(answer, rep)
        assert "실적이 개선" not in cleaned


class TestV5Placeholder:
    def test_unresolved_slot_blocked(self):
        """D1: 치환 실패한 자리표시자는 생성 실패로 간주한다."""
        rep = verify("매출액은 {{F1.value}}원입니다. [C1]", context=CTX,
                     citation_ids=CITES, requirements=[])
        assert not rep.ok
        assert rep.v5_unresolved_placeholders


class TestStripFailing:
    def test_removes_only_bad_sentence(self):
        bad = ("매출액은 300,870,903백만원입니다. 주가가 상승할 것으로 전망됩니다. "
               "영업이익은 999,999백만원입니다.")
        rep = verify(bad, context=CTX, citation_ids=set(), requirements=[])
        cleaned = strip_failing_sentences(bad, rep)
        assert "300,870,903" in cleaned
        assert "전망" not in cleaned
        assert "999,999" not in cleaned


class TestAbstention:
    def test_prediction_detected(self):
        assert detect_prediction("삼성전자 주가가 앞으로 오를까요?")
        assert detect_prediction("목표주가 알려줘")
        assert not detect_prediction("삼성전자 2024년 매출액은?")

    def test_unsupported_source(self):
        assert detect_unsupported("뉴스에서 본 소식") == "뉴스·보도 자료"
        assert detect_unsupported("애널리스트 리포트 요약") is not None
        assert detect_unsupported("사업보고서 요약") is None

    def _base(self, **kw):
        args = dict(question="q", corp_codes=["00126380"], years=[2024],
                    metric_key="revenue", has_facts=True, top_search_score=0.9,
                    threshold=0.35, mentions_company=True)
        args.update(kw)
        return decide(**args)

    def test_proceeds_when_grounded(self):
        assert self._base(question="삼성전자 2024년 매출액은?") is None

    def test_forbidden_prediction(self):
        a = self._base(question="삼성전자 주가 오를까요?")
        assert a and a.reason == "forbidden_prediction"

    def test_out_of_universe(self):
        a = self._base(question="△△전자 매출액", corp_codes=[], has_facts=False,
                       top_search_score=0.0)
        assert a and a.reason in ("out_of_universe", "ambiguous")

    def test_out_of_period(self):
        a = self._base(question="2019년 매출액", years=[2019], has_facts=False,
                       top_search_score=0.0)
        assert a and a.reason == "out_of_period"

    def test_low_unit_confidence_blocks_comparison(self):
        a = self._base(question="A와 B 비교", is_comparison=True, unit_low_confidence=True)
        assert a and a.reason == "low_unit_confidence"

    def test_render_includes_facts_and_followup(self):
        """AC-AB2: 거부만 하지 않고 확인 가능 사실 + 역질문을 함께 준다."""
        a = self._base(question="주가 오를까요?",
                       available_facts=["2024년 매출액 300.9조원 [C1]"])
        text = a.render()
        assert "300.9조원" in text
        assert "알려주시면" in text or "질문" in text


class TestPII:
    """개인정보 마스킹 (평가지표 6). 공시에 임원 생년월일·성별이 실재한다."""

    def test_detects_pii_request(self):
        from dart_agent.agent.pii import is_pii_request

        assert is_pii_request("삼성전자 임원 생년월일 알려줘")
        assert is_pii_request("임원 성별과 나이는?")
        assert is_pii_request("담당자 연락처 알려줘")

    def test_company_level_question_is_not_pii(self):
        """회사 단위 정보는 차단하지 않는다 — 과잉 차단은 지표 1·3을 해친다."""
        from dart_agent.agent.pii import is_pii_request

        assert not is_pii_request("삼성전자 임원 수는 몇 명인가?")
        assert not is_pii_request("2024년 매출액은?")
        assert not is_pii_request("임원 보수 총액은?")

    def test_section_detection(self):
        from dart_agent.agent.pii import is_pii_section

        assert is_pii_section("VIII-1", "1. 임원 및 직원 등의 현황")
        assert not is_pii_section("III-2-2", "연결 손익계산서")

    def test_mask_removes_identifiers(self):
        from dart_agent.agent.pii import mask

        out = mask("홍길동 1965년 03월생 hong@x.com 010-1234-5678 900101-1234567")
        assert "1965년 03월" not in out
        assert "hong@x.com" not in out
        assert "010-1234-5678" not in out
        assert "900101-1234567" not in out


class TestJosa:
    def test_batchim_selection(self):
        from dart_agent.numbers import josa

        assert josa("삼성전자") == "가"
        assert josa("LG이노텍") == "이"
        assert josa("기아") == "가"

    def test_non_korean_defaults_to_no_batchim(self):
        from dart_agent.numbers import josa

        assert josa("NAVER") == "가"


class TestPredictionCoverage:
    """🔴 투자 권유 표현 — Gold Set이 잡아낸 누락의 회귀 가드 (2026-08-18).

    "사도 될까"만 있어서 **"지금 사야 할까요?"가 통과**했다.
    답하면 정확성·정보한계대응 두 채점 항목에서 동시에 감점된다.
    """

    BLOCK = [
        "CJ제일제당 주식을 지금 사야 할까요?",
        "삼성전자 지금 사도 될까요?",
        "이거 팔아야 할까요?",
        "매입해야 하나요?",
        "지금 매수해도 되나요?",
        "비중 확대해도 될까요?",
        "담아도 되나요?",
        "들어가도 될까요?",
        "삼성전자 매수 추천해줘",
        "목표주가 알려줘",
    ]
    # 🔴 과잉 차단 방지 — 이쪽이 막히면 정상 문항을 잃는다
    ALLOW = [
        "삼성전자의 2024년 매출액은?",
        "삼성전자가 반도체를 파는 회사인가?",
        "자기주식 처분 내역을 알려줘",
        "자기주식 취득 규모는?",
        "매출채권 처분 손실은?",
        "매출액이 증가했는가?",
        "주요 제품을 파는 시장은 어디인가",
        "현재 자산총계는 얼마인가",
    ]

    @pytest.mark.parametrize("q", BLOCK)
    def test_blocks_investment_advice(self, q):
        assert detect_prediction(q), f"투자 권유가 통과됨: {q}"

    @pytest.mark.parametrize("q", ALLOW)
    def test_does_not_overblock(self, q):
        assert not detect_prediction(q), f"정상 질의가 차단됨: {q}"


class TestPeriodScope:
    """🔴 누적/당기 파싱 — scope_split 0/20의 회귀 가드 (2026-08-18).

    period_scope가 정렬 순위로만 쓰이고 필터로는 안 쓰여서
    "상반기 매출"에 **연간 값**이 반환됐다.
    """

    CASES = [
        ("NAVER의 2024년 상반기 누적 연결기준 매출액은?", "HYA"),
        ("삼성전자 2025년 상반기 매출액", "HYA"),
        ("LG에너지솔루션의 2025년 2분기(3개월) 영업이익은?", "HYQ"),
        ("삼성전자 2025년 2분기 영업이익", "HYQ"),
        ("삼성전자 2026년 1분기 매출액", "QTQ"),
        ("삼성전자 2026년 3분기 누적 매출액", "QTA"),
        ("삼성전자 2024년 연간 매출액", "FY"),
        ("삼성전자의 2024년 연결기준 매출액은?", None),  # 기간 무명시 → 기존 우선순위
    ]

    @pytest.mark.parametrize("q,want", CASES)
    def test_parse_scope(self, q, want):
        from dart_agent.retrieval.section_map import parse_scope

        assert parse_scope(q) == want, f"{q!r} → {parse_scope(q)} (기대 {want})"

    def test_fact_query_accepts_scope(self):
        """`scope` 파라미터가 존재해야 한다 — 없으면 기간 지정이 무시된다."""
        import inspect

        from dart_agent.agent.tools import fact_query

        assert "scope" in inspect.signature(fact_query).parameters
