"""답변 표현 품질 회귀 가드 (2026-08-18, 실제 답변 시연 중 발견).

정답이어도 **읽는 사람에게 손해인 출력**이 있다. 아래 둘은 골드셋을 100%로
통과하면서도 눈으로 보면 바로 문제인 것들이라, 자동 채점만으로는 못 잡는다.

  1. 기권하면서 근거 6건을 붙였다 — "근거 없음"이라 말하며 근거를 다는 자기모순
  2. 같은 회사·같은 목차를 보고서만 바꿔 3번 출력 — 정보량 1, 분량 3
"""

import pytest

from dart_agent.agent.orchestrator import Orchestrator


class TestCitedOnly:
    """🔴 인용 목록은 답변이 **실제로 참조한** 것만 담는다."""

    CITES = [{"id": f"C{i}", "section": f"III-{i}"} for i in range(1, 7)]

    def test_unreferenced_citations_dropped(self):
        """실측: 투자의견 기권 답변에 `[C1] III-7-1` 등 6건이 붙어 있었다."""
        answer = "공시에 근거가 없는 미래 예측이나 투자 의견은 제공하지 않습니다."
        assert Orchestrator._cited_only(answer, self.CITES) == []

    def test_referenced_citations_kept(self):
        answer = "매출액은 300,870,903백만원입니다 [C2]. 영업이익은 [C5]를 참고하세요."
        got = [c["id"] for c in Orchestrator._cited_only(answer, self.CITES)]
        assert got == ["C2", "C5"]

    def test_order_follows_citation_list_not_answer(self):
        """번호 순서를 유지한다 — 사용자가 [C1][C2]로 찾아보기 때문."""
        answer = "[C5] 그리고 [C2]"
        got = [c["id"] for c in Orchestrator._cited_only(answer, self.CITES)]
        assert got == ["C2", "C5"]

    def test_partial_match_not_confused(self):
        """`[C1]`이 `[C11]`을 물면 안 된다."""
        cites = [{"id": "C1"}, {"id": "C11"}]
        got = [c["id"] for c in Orchestrator._cited_only("근거 [C11] 참조", cites)]
        assert got == ["C11"]

    def test_empty_citations_safe(self):
        assert Orchestrator._cited_only("아무 근거 없음", []) == []


class TestSectionDedup:
    """🔴 같은 회사·같은 목차의 반복 출력 차단.

    실측: "메리츠금융지주의 배당에 관한 사항은?" → 분기보고서 · [기재정정]분기보고서
    · 사업보고서의 거의 동일한 원문 3개가 연달아 나왔다.
    """

    @staticmethod
    def _sections():
        # 조회는 base_year/base_month DESC 정렬 → 첫 항목이 최신이다
        return [
            {"corp_name": "메리츠금융지주", "path": "III-6", "doc_id": "d1",
             "report_nm": "분기보고서 (2026.03)", "title": "6. 배당에 관한 사항",
             "text": "당사는 중기 주주환원 정책을 결정하여 공시하였습니다."},
            {"corp_name": "메리츠금융지주", "path": "III-6", "doc_id": "d2",
             "report_nm": "[기재정정]분기보고서 (2026.03)", "title": "6. 배당에 관한 사항",
             "text": "당사는 중기 주주환원 정책을 결정하여 공시하였습니다."},
            {"corp_name": "메리츠금융지주", "path": "III-6", "doc_id": "d3",
             "report_nm": "사업보고서 (2025.12)", "title": "6. 배당에 관한 사항",
             "text": "당사는 중기 주주환원 정책을 결정하여 공시하였습니다."},
        ]

    def _compose_sections(self, sections):
        """_compose의 섹션 블록만 떼어내 검사한다 (DB 없이 돌리기 위함)."""
        import re

        from dart_agent.agent import pii

        cites = [{"id": "C1", "doc_id": "d1", "section": "III-6"}]
        lines, seen = [], set()
        for s in sections:
            key = (s.get("corp_name"), s.get("path"))
            if key in seen:
                continue
            seen.add(key)
            cid = next((c["id"] for c in cites if c["doc_id"] == s["doc_id"]
                        and c.get("section", "").startswith(s["path"])), "C1")
            snippet = re.sub(r"\s+", " ", s["text"])[:400]
            if pii.is_pii_section(s.get("path"), s.get("title")):
                snippet = pii.mask(snippet)
            lines.append(f"{s['corp_name']} {s['report_nm']} {s['title']}: {snippet} [{cid}]")
            if len(seen) >= 3:
                break
        return lines

    def test_same_corp_same_path_collapsed_to_one(self):
        lines = self._compose_sections(self._sections())
        assert len(lines) == 1, f"중복 제거 실패: {len(lines)}줄"

    def test_newest_report_wins(self):
        """정렬상 첫 항목이 최신이므로 그것을 남긴다."""
        lines = self._compose_sections(self._sections())
        assert "분기보고서 (2026.03)" in lines[0]
        assert "[기재정정]" not in lines[0]

    def test_different_corps_all_kept(self):
        """🔴 과잉 제거 방지 — 섹터 질의는 여러 회사를 보여줘야 한다."""
        secs = [dict(s, corp_name=f"회사{i}") for i, s in enumerate(self._sections())]
        assert len(self._compose_sections(secs)) == 3

    def test_different_paths_all_kept(self):
        secs = [dict(s, path=f"II-{i+1}") for i, s in enumerate(self._sections())]
        assert len(self._compose_sections(secs)) == 3

    def test_caps_at_three(self):
        secs = [dict(self._sections()[0], corp_name=f"회사{i}") for i in range(10)]
        assert len(self._compose_sections(secs)) == 3
