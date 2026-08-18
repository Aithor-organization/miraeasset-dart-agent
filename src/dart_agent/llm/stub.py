"""StubProvider — 키 없는 환경에서 파이프라인을 살려두는 결정론 스텁 (SPEC §8 AC-L3).

🔴 이것은 답변을 '가짜로 만드는' 장치가 아니다.
   설계 D1에 따라 수치·근거는 이미 Fact Store에서 결정론적으로 확보되어 있고,
   Stub은 그 확보된 사실을 한국어 문장으로 조립하는 템플릿 역할만 한다.
   따라서 stub 경로의 답변도 근거를 갖고 검증기 V1~V5를 통과한다.

키가 주입되면 ClovaProvider가 같은 사실 위에 더 자연스러운 서술을 생성한다 —
품질은 올라가지만 정답성은 stub에서도 이미 성립한다.
"""

from __future__ import annotations

from typing import Any

from .provider import LLMResponse


class StubProvider:
    name = "stub"

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
        max_tokens: int = 8192,  # ClovaProvider와 시그니처 일치 (교체 가능성 보존)
        temperature: float = 0.1,
    ) -> LLMResponse:
        # 도구 계획을 요구하는 호출에는 빈 tool_calls를 반환한다.
        # → Orchestrator가 규칙 기반 fast-path로 폴백한다 (AC-0).
        last_user = next(
            (m.get("content", "") for m in reversed(messages) if m.get("role") == "user"), ""
        )
        return LLMResponse(
            content="",
            tool_calls=[],
            thinking=f"[stub] LLM 미사용 — 결정론 경로로 처리합니다. 질의 길이={len(last_user)}",
            usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            model="stub",
            stubbed=True,
        )
