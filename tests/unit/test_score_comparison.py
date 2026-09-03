"""채점기 comparison 승자 판정 — 문형·극성 회귀 방지 (2026-09-03).

배경: 골드셋 CMP-002가 **정답인데 오답 처리**됐다.
  "한미반도체의 매출액은 5,589.2억원으로 …, 이는 현대건설의 32.7조원보다 낮습니다"
서술 계층(HCX)이 실행마다 어순을 바꾸므로 같은 문항이 통과했다 실패했다 했다 —
채점기가 오탐하면 **진짜 회귀를 못 잡는다**.

🔴 핵심은 극성이다: `보다 높다`면 문두 주어가 승자, `보다 낮다`면 패자다.
   한쪽만 보면 절반을 놓친다.
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "score", pathlib.Path(__file__).resolve().parents[2] / "eval" / "score.py")
score = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(score)

ITEM = {
    "kind": "comparison",
    "expect_contains": ["현대건설"],
    "meta": {"a": "한미반도체", "b": "현대건설", "winner": "현대건설"},
}


def _grade(answer: str):
    return score.grade(ITEM, {"answer": answer, "retrieved_context": "x",
                              "citations": [{"id": "C1"}]})


PASS = [
    # 보다-낮 문형 — 문두가 패자 (실측 CMP-002)
    "한미반도체의 2024년 매출액은 5,589.2억원으로, 이는 현대건설의 32.7조원보다 낮습니다.",
    # 보다-높 문형 — 문두가 승자
    "현대건설의 2024년 매출액은 32.7조원으로, 한미반도체의 5,589.2억원보다 높습니다.",
    # 명시 선언 — 패자가 먼저 나와도 정답
    "한미반도체와 현대건설 중 매출액이 더 큰 기업은 현대건설입니다.",
    "한미반도체보다 현대건설이 더 큽니다.",
]

FAIL = [
    # 승자를 뒤집어 선언
    "현대건설의 매출액은 32.7조원으로, 한미반도체의 5,589.2억원보다 낮습니다.",
    "한미반도체의 매출액이 현대건설보다 더 큽니다.",
    "더 큰 기업은 한미반도체입니다.",
]


@pytest.mark.parametrize("ans", PASS)
def test_correct_answers_pass(ans):
    ok, why = _grade(ans)
    assert ok, f"정답을 오답 처리: {why} | {ans}"


@pytest.mark.parametrize("ans", FAIL)
def test_wrong_answers_fail(ans):
    ok, why = _grade(ans)
    assert not ok, f"오답을 통과시킴: {ans}"


def test_missing_winner_fails():
    ok, _ = _grade("한미반도체의 매출액은 5,589.2억원입니다.")
    assert not ok, "정답 기업 미언급인데 통과"
