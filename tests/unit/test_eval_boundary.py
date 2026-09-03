"""boundary·regression 채점 + API 계약 경계 회귀 가드.

🔴 이 파일이 지키는 실사고 (2026-08-23):
   810자 질의에 FastAPI가 **422 + `detail`만** 반환해 계약 5필드가 통째로
   빠졌다. `max_length=500`을 Query에 걸어둔 탓이다 — 거부가 아니라 절단이
   맞다. 평가 문항이 길면 그 문항은 `answer` 없이 0점이 된다.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "eval"))
from score import grade  # noqa: E402

CONTRACT = ("question_id", "question", "retrieved_context", "think_trace", "answer")


@pytest.fixture(scope="module")
def handler():
    """라우트 핸들러 직접 호출.

    ⚠️ 한계: `Query(max_length=...)` 같은 **전송 계층 검증은 타지 않는다**.
       바로 그래서 810자 422 버그가 이 스위트를 통과했다 —
       스위트가 구조적으로 볼 수 없는 층에 있었기 때문이다.
       수정은 절단을 핸들러 안으로 옮겼으므로 이제는 여기서 잡힌다.
       HTTP 계층 자체는 실서버 curl로 별도 확인한다 (RUNBOOK).
    """
    from dart_agent.api import server

    server._startup()
    if not server._STATE.get("ready"):
        pytest.skip("인덱스가 없는 환경에서는 장문 답변 경계의 실행 경로를 검증할 수 없음")

    def call(question: str, question_id: str = "BND") -> dict:
        resp = server.answer(question_id=question_id, question=question)
        return json.loads(bytes(resp.body).decode("utf-8"))

    return call


# ── 채점 로직 ────────────────────────────────────────────────────────────────

def _resp(**kw):
    base = {k: "" for k in CONTRACT}
    base.update(kw)
    return base


class TestBoundaryGrade:
    def test_계약_필드_누락은_실패(self):
        ok, why = grade({"kind": "boundary"}, {"detail": "String too long"})
        assert not ok and "계약 필드 누락" in why

    def test_기권_기대인데_답변하면_실패(self):
        item = {"kind": "boundary", "expect_abstain": True}
        ok, why = grade(item, _resp(answer="삼성전자 매출은 …", abstained=False))
        assert not ok and "기권해야" in why

    def test_답변_기대인데_기권하면_실패(self):
        """🔴 절단 케이스(BND-004) — 잘라서라도 답해야 한다."""
        item = {"kind": "boundary", "forbid_abstain": True}
        ok, why = grade(item, _resp(abstained=True, abstain_reason="too_long"))
        assert not ok and "답해야" in why

    def test_계약만_지키면_통과(self):
        ok, _ = grade({"kind": "boundary"}, _resp(answer="…", abstained=False))
        assert ok


class TestRegressionGrade:
    ITEM = {"kind": "regression", "expect_value_krw": 7664877701977,
            "max_latency_ms": 60000,
            "meta": {"base_kind": "single_value", "incident": "SV-022 578초"}}

    def test_지연_상한_초과는_값이_맞아도_실패(self):
        """정확도가 100%여도 시간으로 잃는다 — 그게 그 사고의 성질이었다."""
        ok, why = grade(self.ITEM, _resp(answer="7,664,877,701,977원"),
                        elapsed_ms=578_200)
        assert not ok
        assert "회귀" in why and "SV-022" in why

    def test_상한_내면서_값도_맞으면_통과(self):
        ok, why = grade(self.ITEM, _resp(answer="7,664,877,701,977원"),
                        elapsed_ms=12_500)
        assert ok and "회귀 없음" in why

    def test_상한_내여도_값이_틀리면_실패(self):
        ok, _ = grade(self.ITEM, _resp(answer="1,234원"), elapsed_ms=1_000)
        assert not ok

    def test_지연_미측정이면_값으로만_판정(self):
        ok, _ = grade(self.ITEM, _resp(answer="7,664,877,701,977원"), elapsed_ms=None)
        assert ok


# ── API 계약 경계 ────────────────────────────────────────────────────────────

class TestContractBoundary:
    def test_초장문도_계약_5필드_유지(self, handler):
        """🔴 실사고 회귀 가드 — 이전엔 422 + detail만 나왔다."""
        body = handler("삼성전자의 2024년 연결기준 매출액은 얼마인가?" * 30)
        assert all(k in body for k in CONTRACT), f"계약 위반: {list(body)[:5]}"
        assert not body.get("abstained"), "절단해서라도 답해야 한다"
        # 절단 사실을 근거 산출물에 남긴다 (D4)
        assert "절단" in body["think_trace"]

    def test_빈_질문은_기권으로_답한다(self, handler):
        body = handler("")
        assert all(k in body for k in CONTRACT)
        assert body["abstained"] is True
        assert body["abstain_reason"] == "empty_question"

    def test_공백만_기권(self, handler):
        assert handler("   ")["abstained"] is True

    def test_특수문자만_예외_미발생(self, handler):
        body = handler("!@#$%^&*()_+{}|:\"<>?~`")
        assert all(k in body for k in CONTRACT)

    def test_question_id도_절단된다(self, handler):
        body = handler("삼성전자 매출", question_id="Q" * 500)
        assert len(body["question_id"]) <= 200

    def test_질의_원문은_상한까지만_에코된다(self, handler):
        body = handler("가" * 900)
        assert len(body["question"]) <= 500
