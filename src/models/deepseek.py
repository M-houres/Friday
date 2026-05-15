"""DeepSeek 模型适配器"""

import json
import logging
import time
from typing import AsyncIterator

import httpx

from src.config import settings
from src.models.base import Message, ModelProvider, ModelResponse, ToolCall

logger = logging.getLogger(__name__)

COST_PER_M = {
    "deepseek-chat": (0.14, 0.28),
    "deepseek-reasoner": (0.55, 2.19),
}


class DeepSeekProvider(ModelProvider):
    provider_name = "deepseek"

    def __init__(self):
        self.api_key = settings.deepseek_api_key
        self.base_url = settings.deepseek_base_url.rstrip("/")
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(120, connect=10),
        )

    async def chat(
        self,
        messages: list[Message],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: list[dict] | None = None,
        response_format: dict | None = None,
    ) -> ModelResponse:
        model = model or settings.default_model
        start = time.monotonic()

        body = {
            "model": model,
            "messages": [self._format_message(m) for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            body["tools"] = tools
        if response_format:
            body["response_format"] = response_format

        resp = await self.client.post("/chat/completions", json=body)
        resp.raise_for_status()
        data = resp.json()

        choice = data["choices"][0]
        msg = choice["message"]
        usage = data.get("usage", {})

        tool_calls = None
        if msg.get("tool_calls"):
            tool_calls = [
                ToolCall(id=tc.get("id", ""), name=tc["function"]["name"], arguments=json.loads(tc["function"]["arguments"]) if isinstance(tc["function"]["arguments"], str) else tc["function"]["arguments"])
                for tc in msg["tool_calls"]
            ]

        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        cost = self.estimate_cost(model, prompt_tokens, completion_tokens)
        latency = (time.monotonic() - start) * 1000

        return ModelResponse(
            content=msg.get("content", "") or "",
            model=model,
            tokens_used=prompt_tokens + completion_tokens,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency,
            cost_usd=cost,
            finish_reason=choice.get("finish_reason", "stop"),
            metadata={"raw": data},
        )

    async def chat_stream(
        self,
        messages: list[Message],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: list[dict] | None = None,
    ) -> AsyncIterator[str]:
        model = model or settings.default_model

        body = {
            "model": model,
            "messages": [self._format_message(m) for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if tools:
            body["tools"] = tools

        async with self.client.stream("POST", "/chat/completions", json=body) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    data_str = line[6:]
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                        delta = chunk["choices"][0].get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            yield content
                    except json.JSONDecodeError:
                        continue

    async def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        model = model or "text-embedding-3-small"
        resp = await self.client.post(
            "/embeddings",
            json={"model": model, "input": texts},
        )
        resp.raise_for_status()
        data = resp.json()
        return [item["embedding"] for item in data["data"]]

    def get_model_list(self) -> list[str]:
        return ["deepseek-chat", "deepseek-reasoner"]

    def estimate_cost(self, model: str, prompt_tokens: int, completion_tokens: int) -> float:
        prices = COST_PER_M.get(model)
        if not prices:
            return 0.0
        return (prompt_tokens * prices[0] + completion_tokens * prices[1]) / 1_000_000

    def _format_message(self, msg: Message) -> dict:
        formatted = {"role": msg.role, "content": msg.content}
        if msg.tool_calls:
            formatted["tool_calls"] = [
                {"id": tc.id, "type": "function", "function": {"name": tc.name, "arguments": json.dumps(tc.arguments, ensure_ascii=False)}}
                for tc in msg.tool_calls
            ]
        if msg.tool_call_id:
            formatted["tool_call_id"] = msg.tool_call_id
        if msg.name:
            formatted["name"] = msg.name
        return formatted

    async def close(self):
        await self.client.aclose()
