"""LLM / Embedding 프로바이더 계약 (SPEC §8).

AC-L4: 이 계층 밖에서 httpx/requests 직접 호출 금지.
AC-L2: CLOVA_API_KEY 미설정 시 StubProvider로 자동 폴백 — 서버는 계속 동작한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class LLMResponse:
    content: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    thinking: str = ""
    usage: dict[str, int] = field(default_factory=dict)
    model: str = ""
    stubbed: bool = False
    # 🔴 추론이 토큰 한도에 잘려 content가 비었음 (HCX-007 thinking 모델 실측 함정).
    #    HTTP는 200이라 오류로 보이지 않는다 — 호출자는 반드시 이 값을 확인하고
    #    결정론 템플릿으로 강등해야 한다. 상세: clova.py 모듈 docstring
    truncated: bool = False

    @property
    def usable(self) -> bool:
        """이 응답을 답변 생성에 써도 되는가."""
        return bool(self.content.strip() or self.tool_calls) and not self.truncated


@runtime_checkable
class LLMProvider(Protocol):
    name: str

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
        max_tokens: int = 8192,  # 🔴 낮추면 추론 절단 — clova.py docstring 참조
        temperature: float = 0.1,
    ) -> LLMResponse: ...


@runtime_checkable
class EmbeddingProvider(Protocol):
    name: str
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


def build_providers(cfg) -> tuple[LLMProvider, EmbeddingProvider | None, list[str]]:
    """설정에 따라 프로바이더를 만든다. 반환: (llm, embedding|None, 경고목록).

    키가 없으면 embedding은 None이고, 검색은 BM25 단독으로 동작한다 (AC-R5).
    """
    notes: list[str] = []
    if cfg.has_llm:
        from .clova import ClovaEmbeddingProvider, ClovaProvider

        return (
            ClovaProvider(cfg.clova_api_key, cfg.clova_base_url, cfg.chat_model),
            ClovaEmbeddingProvider(cfg.clova_api_key, cfg.clova_base_url, cfg.embedding_model),
            notes,
        )

    from .stub import StubProvider

    notes.append(
        "CLOVA_API_KEY 미설정 → StubProvider 사용. "
        "결정론 경로(fact/section/event/compute)는 정상 동작하나 "
        "LLM 서술 생성·벡터 검색·리랭킹은 비활성입니다."
    )
    return StubProvider(), None, notes
