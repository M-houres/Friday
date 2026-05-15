"""Anthropic Claude 模型适配器"""

import json
import logging
import time

from src.config import settings
from src.models.base import Message, ModelProvider, ModelResponse

logger = logging.getLogger(__name__)

COST_PER_M = {
    "claude-sonnet-4-20250514": (3.0, 15.0),
    "claude-haiku-4-5": (0.80, 4.0),
    "claude-opus-4-1": (15.0, 75.0),
}


class AnthropicProvider(ModelProvider):
    provider_name = "anthropic"

    def __init__(self):
        self.api_key = settings.anthropic_api_key
        if not self.api_key:
            self._available = False
            return
        self._available = True
        import httpx
        self.client = httpx.AsyncClient(
            base_url="https://api.anthropic.com/v1",
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(120, connect=10),
        )

    def is_available(self) -> bool:
        return self._available and bool(self.api_key)

    async def chat(
        self,
        messages: list[Message],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: list[dict] | None = None,
        response_format: dict | None = None,
    ) -> ModelResponse:
        if not self.is_available():
            raise RuntimeError("Anthropic provider not configured")

        model = model or "claude-sonnet-4-20250514"
        start = time.monotonic()

        system_prompt = ""
        formatted_messages = []
        for m in messages:
            if m.role == "system":
                system_prompt += m.content + "\n"
            else:
                formatted_messages.append({"role": m.role, "content": m.content})

        body = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": formatted_messages,
        }
        if system_prompt.strip():
            body["system"] = system_prompt.strip()
        if tools:
            body["tools"] = tools

        resp = await self.client.post("/messages", json=body)
        resp.raise_for_status()
        data = resp.json()

        content = ""
        for block in data.get("content", []):
            if block.get("type") == "text":
                content += block.get("text", "")

        usage = data.get("usage", {})
        prompt_tokens = usage.get("input_tokens", 0)
        completion_tokens = usage.get("output_tokens", 0)
        cost = self.estimate_cost(model, prompt_tokens, completion_tokens)
        latency = (time.monotonic() - start) * 1000

        return ModelResponse(
            content=content,
            model=model,
            tokens_used=prompt_tokens + completion_tokens,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency,
            cost_usd=cost,
            finish_reason=data.get("stop_reason", "end_turn"),
        )

    async def chat_stream(
        self,
        messages: list[Message],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: list[dict] | None = None,
    ):
        """Anthropic SSE 流式输出"""
        if not self.is_available():
            raise RuntimeError("Anthropic provider not configured")

        model = model or "claude-sonnet-4-20250514"

        system_prompt = ""
        formatted_messages = []
        for m in messages:
            if m.role == "system":
                system_prompt += m.content + "\n"
            else:
                formatted_messages.append({"role": m.role, "content": m.content})

        body = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": formatted_messages,
            "stream": True,
        }
        if system_prompt.strip():
            body["system"] = system_prompt.strip()
        if tools:
            body["tools"] = tools

        async with self.client.stream("POST", "/messages", json=body) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data_str = line[6:]
                try:
                    event = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                event_type = event.get("type", "")

                if event_type == "content_block_delta":
                    delta = event.get("delta", {})
                    if delta.get("type") == "text_delta":
                        yield delta.get("text", "")

                elif event_type == "message_stop":
                    break

    async def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        raise NotImplementedError("Anthropic does not provide embeddings API")

    def get_model_list(self) -> list[str]:
        return ["claude-sonnet-4-20250514", "claude-haiku-4-5", "claude-opus-4-1"]

    def estimate_cost(self, model: str, prompt_tokens: int, completion_tokens: int) -> float:
        prices = COST_PER_M.get(model)
        if not prices:
            return 0.0
        return (prompt_tokens * prices[0] + completion_tokens * prices[1]) / 1_000_000

    async def close(self):
        if hasattr(self, "client"):
            await self.client.aclose()
