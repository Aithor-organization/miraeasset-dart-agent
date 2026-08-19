"""HyperCLOVA X 프로바이더 — OpenAI 호환 엔드포인트 사용 (SPEC §8 AC-L1).

플랫폼 사실 (NCP 공식 문서, 2026-07-30 확인):
  Base URL : https://clovastudio.stream.ntruss.com/v1/openai
  인증     : Authorization: Bearer nv-***
  지원     : messages/model/stream/max_completion_tokens/temperature/tools/tool_choice/
             response_format/top_p/stop  (필드는 snake_case)
  미지원   : frequency_penalty/presence_penalty/logit_bias/user/n>1
  모델     : HCX-007  128K ctx / 32,768 out / function calling ✓ / structured output ✓ / thinking ✓
  임베딩   : bge-m3 (v2)  8,192 tokens / 1024 dim / cosine

🔴 과제 제약: LLM은 HyperCLOVA X만 허용된다. 임베딩·리랭킹도 CLOVA API로 통일해
   제약 해석 여하에 무관하게 안전한 구성을 만든다 (설계 D6).

🔴 **thinking 모델의 빈 응답 함정 (2026-08-11 실측)**
HCX-007은 답변 전에 추론 토큰을 소비하고, 그 추론이 `max_completion_tokens`에 **잘리면
content가 빈 문자열로 돌아온다**. HTTP는 200이라 오류로 보이지도 않는다.

  max_completion_tokens=2048로 동일 요청 5회 → **3회가 빈 응답**
  (reasoning_tokens가 2047~2048로 정확히 한도에 잘림 = 자연 종료가 아니라 절단)

  max_completion_tokens=8192 + 간결 지시 시스템 프롬프트 → **5/5 성공**
  (추론이 284~1,010에서 자연 종료, completion 중앙값 644)

그래서 기본값은 **8192**이고, 그럼에도 빈 응답이 오면 `LLMResponse.truncated`가 True가 된다.
호출자는 이 값을 보고 결정론 템플릿으로 강등해야 한다 — 빈 문자열을 그대로 답변에 쓰면 안 된다.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx

from .. import counters
from .provider import LLMResponse
from .ratelimit import (MAX_RETRIES, DeadlineExceeded, Pacer, remaining,
                        retry_wait)

log = logging.getLogger(__name__)

# 이보다 적게 남았으면 시도 자체를 하지 않는다 — HCX-007은 추론 모델이라
# 최소 몇 초는 걸린다. 1~2초 남기고 부르면 절단만 만들고 예산을 태운다.
_MIN_ATTEMPT_S = 3.0

# 🔴 read 60초는 재시도 4회와 곱해지면 예산을 압도한다 (실측 계산: 340s/호출).
#    HCX-007 정상 응답은 5~15초라 25초면 충분하고, 그 이상은 이상 상태다.
_READ_TIMEOUT_S = 25.0
_TIMEOUT = httpx.Timeout(connect=5.0, read=_READ_TIMEOUT_S, write=10.0, pool=5.0)


class ClovaError(RuntimeError):
    pass


class ClovaProvider:
    name = "clova"

    def __init__(self, api_key: str, base_url: str, model: str) -> None:
        self._key = api_key
        self._base = base_url.rstrip("/")
        self.model = model
        self._pacer = Pacer()

    @staticmethod
    def _timeout(deadline: float | None) -> httpx.Timeout:
        """read 타임아웃을 **잔여 예산 이하로** 자른다.

        🔴 고정 60초는 예산을 넘긴다. 예산이 20초 남았는데 60초를 기다리면
           그 40초는 응답이 와도 쓸 수 없는 시간이다 — 잘라야 한다.
        """
        read = min(_READ_TIMEOUT_S, max(_MIN_ATTEMPT_S, remaining(deadline) - 1.0))
        return httpx.Timeout(connect=5.0, read=read, write=10.0, pool=5.0)

    def _post_with_retry(self, payload: dict[str, Any],
                         deadline: float | None = None) -> httpx.Response:
        """429/5xx를 헤더가 알려준 만큼 기다렸다가 재시도한다.

        🔴 재시도가 없으면 **실패가 실패를 부른다** — 429는 즉시 반환되므로
           다음 호출이 곧바로 또 두드리고, 리셋될 틈이 없다 (실측: 골드셋
           177문항 중 60건 폐기. 정작 호출률은 한도의 1/5이었다).
        """
        last: Exception | None = None
        for attempt in range(MAX_RETRIES + 1):
            # 🔴 남은 예산 안에서만 시도한다. 예산을 넘길 호출을 시작하면
            #    그 요청은 어차피 평가 타임아웃으로 0점이고, 기다린 시간은
            #    순손실이다 — 차라리 즉시 결정론 템플릿으로 강등하는 게 낫다.
            left = remaining(deadline)
            if left <= _MIN_ATTEMPT_S:
                raise DeadlineExceeded(f"잔여 {left:.1f}s — 호출 생략")
            self._pacer.wait(deadline)            # 한도에 닿기 전에 선제적으로 쉰다
            try:
                with httpx.Client(timeout=self._timeout(deadline)) as client:
                    r = client.post(
                        f"{self._base}/chat/completions",
                        headers={
                            "Authorization": f"Bearer {self._key}",
                            "Content-Type": "application/json",
                        },
                        json=payload,
                    )
            except httpx.HTTPError as exc:        # 네트워크 오류 — 호출자가 강등 처리
                raise ClovaError(f"clova transport error: {exc}") from exc

            if r.status_code < 400:
                counters.bump("llm_calls")
                self._pacer.observe(r.headers)
                return r
            if r.status_code == 429:
                counters.bump("llm_429")

            # 429(한도)와 5xx(일시 장애)만 재시도한다. 4xx는 재시도해도 같은 답이다.
            if r.status_code != 429 and r.status_code < 500:
                raise ClovaError(f"clova {r.status_code}: {r.text[:300]}")

            last = ClovaError(f"clova {r.status_code}: {r.text[:300]}")
            if attempt == MAX_RETRIES:
                break
            wait = retry_wait(r.headers, attempt)
            # 잘 시간 + 다음 시도 최소치가 예산을 넘으면 재시도가 무의미하다
            if wait + _MIN_ATTEMPT_S >= remaining(deadline):
                raise DeadlineExceeded(
                    f"재시도 대기 {wait:.1f}s > 잔여 {remaining(deadline):.1f}s")
            log.info("clova %s — %.1fs 후 재시도 (%d/%d)",
                     r.status_code, wait, attempt + 1, MAX_RETRIES)
            time.sleep(wait)

        raise last if last else ClovaError("clova: 재시도 소진")

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
        max_tokens: int = 8192,  # 🔴 2048은 3/5 확률로 빈 응답 — 모듈 docstring 참조
        temperature: float = 0.1,
        deadline: float | None = None,   # time.monotonic() 기준. 넘기면 DeadlineExceeded
    ) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_completion_tokens": max_tokens,
            "temperature": temperature,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if response_format:
            payload["response_format"] = response_format

        r = self._post_with_retry(payload, deadline)

        data = r.json()
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        content = msg.get("content") or ""
        tool_calls = _norm_tool_calls(msg.get("tool_calls") or [])
        usage = data.get("usage") or {}

        # 🔴 추론 절단 감지: content도 tool_calls도 없는데 200이 온 경우.
        #    finish_reason='length' 또는 reasoning이 한도에 닿았으면 절단으로 본다.
        detail = usage.get("completion_tokens_details") or {}
        reasoning = int(detail.get("reasoning_tokens") or 0)
        truncated = not content.strip() and not tool_calls and (
            choice.get("finish_reason") == "length" or reasoning >= max_tokens * 0.95
        )
        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            thinking=msg.get("reasoning_content") or msg.get("thinking_content") or "",
            usage=usage,
            model=data.get("model") or self.model,
            truncated=truncated,
        )


def _norm_tool_calls(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """OpenAI 형식 tool_calls → {name, arguments(dict)} 정규화."""
    out: list[dict[str, Any]] = []
    for tc in raw:
        fn = tc.get("function") or {}
        args = fn.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {"_raw": args}
        out.append({"id": tc.get("id"), "name": fn.get("name"), "arguments": args or {}})
    return out


class ClovaEmbeddingProvider:
    name = "clova-bge-m3"
    dim = 1024

    def __init__(self, api_key: str, base_url: str, model: str) -> None:
        self._key = api_key
        self._base = base_url.rstrip("/")
        self.model = model

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            with httpx.Client(timeout=_TIMEOUT) as client:
                r = client.post(
                    f"{self._base}/embeddings",
                    headers={
                        "Authorization": f"Bearer {self._key}",
                        "Content-Type": "application/json",
                    },
                    # encoding_format은 float만 지원 (base64 미지원)
                    json={"model": self.model, "input": texts, "encoding_format": "float"},
                )
        except httpx.HTTPError as exc:
            raise ClovaError(f"clova embed transport error: {exc}") from exc
        if r.status_code >= 400:
            raise ClovaError(f"clova embed {r.status_code}: {r.text[:300]}")
        data = r.json()
        return [item["embedding"] for item in sorted(data["data"], key=lambda d: d["index"])]


class ClovaReranker:
    """CLOVA `/v1/api-tools/reranker` — 🔴 **이름과 달리 순수 리랭커가 아니다** (실측).

    POST /v1/api-tools/reranker
      요청: {"query": str, "documents": [{"id": str, "doc": str}], "maxTokens": int}
      응답: {"result": {"result": "<생성된 답변>",
                        "citedDocuments": [{"id": ...}],
                        "suggestedQueries": [...], "usage": {...}}}

    즉 점수 배열을 주는 리랭커가 아니라 **RAG 답변 생성 + 인용 API**다. 우리는 생성된
    답변은 버리고 `citedDocuments`만 **관련성 필터**로 쓴다 (수치는 Fact Store가 낸다 — D1).

    ⚠️ **실측 주의 (2026-08-11)**
    - 문서가 짧으면 관련 문서도 인용하지 않는다. 한 줄짜리 문장 4건을 넣었더니
      매출액이 명시된 문서를 두고 "정보가 제공되지 않습니다"로 거부했다.
      실제 섹션 본문(수백 자)을 넣으면 정상 인용한다 → **본문을 충분히 실어 보낼 것**.
    - 인용은 보수적이다 (4건 중 1건). 재현율보다 정밀도가 높은 필터로 취급하고,
      **BM25 상위 결과를 이것으로 제거하지는 말 것** (누락 위험). 가산점 용도로만.
    - 호출당 1,000토큰 안팎을 쓴다 (실측 promptTokens 727 + completion 335).
    """

    name = "clova-reranker"

    def __init__(self, api_key: str, native_base: str = "https://clovastudio.stream.ntruss.com"):
        self._key = api_key
        self._base = native_base.rstrip("/")

    def rerank(self, query: str, docs: list[tuple[str, str]], max_tokens: int = 4096):
        if not docs:
            return []
        payload = {
            "query": query,
            "documents": [{"id": i, "doc": d} for i, d in docs],
            "maxTokens": max_tokens,
        }
        try:
            with httpx.Client(timeout=_TIMEOUT) as client:
                r = client.post(
                    f"{self._base}/v1/api-tools/reranker",
                    headers={
                        "Authorization": f"Bearer {self._key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
        except httpx.HTTPError as exc:
            raise ClovaError(f"clova rerank transport error: {exc}") from exc
        if r.status_code >= 400:
            raise ClovaError(f"clova rerank {r.status_code}: {r.text[:300]}")
        result = (r.json().get("result") or {})
        cited = result.get("citedDocuments") or []
        return [c.get("id") for c in cited if c.get("id")]
