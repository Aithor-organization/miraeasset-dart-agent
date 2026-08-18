"""단위·숫자 정규화 회귀 테스트 (AC-TEST3).

실측 근거: 금액 단위가 원/천원/백만원 혼용이고 27%는 미표기 (proposal §2-6-bis).
정규화 실패는 기업 간 비교에서 1,000배 오차를 만든다 (R5b).
"""

from dart_agent.numbers import (UNIT_HIGH, UNIT_LOW, clean_number, detect_unit, fmt_krw,
                                infer_scale_by_magnitude, scale_of, to_krw)


class TestCleanNumber:
    def test_comma(self):
        assert clean_number("300,870,903") == 300870903.0

    def test_negative_parenthesis(self):
        """DART 회계 관행: 괄호 = 음수 (삼성전자 별도 영업이익 실측)."""
        assert clean_number("(11,526,297)") == -11526297.0

    def test_decimal(self):
        assert clean_number("2.32") == 2.32

    def test_missing_forms(self):
        for raw in ("", "-", "－", "—", "N/A", "해당사항 없음", "·"):
            assert clean_number(raw) is None, raw

    def test_none(self):
        assert clean_number(None) is None

    def test_garbage(self):
        assert clean_number("약 300조원 수준") is None


class TestDetectUnit:
    def test_paren_no_space(self):
        assert detect_unit("(단위:천원)") == "천원"

    def test_spaced_colon(self):
        assert detect_unit("(단위 : 백만원)") == "백만원"

    def test_fullwidth_colon(self):
        assert detect_unit("단위：원") == "원"

    def test_absent(self):
        assert detect_unit("구분 제14기 제13기") is None

    def test_unsupported_unit(self):
        assert detect_unit("단위: 달러") is None


class TestScales:
    def test_known(self):
        assert scale_of("백만원") == 1_000_000
        assert scale_of("천원") == 1_000
        assert scale_of("원") == 1
        assert scale_of("억원") == 100_000_000

    def test_unknown(self):
        assert scale_of("달러") is None
        assert scale_of(None) is None


class TestToKrw:
    def test_million_won(self):
        """삼성전자 2024 연결 매출 300,870,903백만원 = 300.87조원."""
        value, conf = to_krw("300,870,903", "백만원")
        assert value == 300_870_903_000_000
        assert conf == UNIT_HIGH

    def test_thousand_won(self):
        """레인보우로보틱스 요약재무 (단위:천원)."""
        value, conf = to_krw("134,567,366", "천원")
        assert value == 134_567_366_000
        assert conf == UNIT_HIGH

    def test_no_unit_refuses_to_guess(self):
        """🔴 AC-U1: 단위 없으면 값을 만들지 않는다. 추측 금지."""
        value, conf = to_krw("300,870,903", None)
        assert value is None
        assert conf == UNIT_LOW

    def test_negative(self):
        value, conf = to_krw("(11,526,297)", "백만원")
        assert value == -11_526_297_000_000
        assert conf == UNIT_HIGH


class TestInferScale:
    def test_recovers_million(self):
        """XBRL 교차검증으로 미표기 단위 역추정 (AC-U1 3-a)."""
        assert infer_scale_by_magnitude("300,870,903", 300_870_903_000_000) == "백만원"

    def test_ambiguous_returns_none(self):
        assert infer_scale_by_magnitude("1", 0) is None

    def test_no_match(self):
        assert infer_scale_by_magnitude("123", 999_999_999_999) is None


class TestFmtKrw:
    def test_trillion(self):
        assert fmt_krw(300_870_903_000_000) == "300.9조원"

    def test_negative(self):
        assert fmt_krw(-11_526_297_000_000).startswith("-")

    def test_none(self):
        assert fmt_krw(None) == "확인 불가"

    def test_hundred_million(self):
        assert "억원" in fmt_krw(34_110_000_000)
