"""流式输出优化 —— 边生成边处理，早期终止"""

import asyncio
import json
import logging
from typing import Any, AsyncIterator, Awaitable, Callable

from src.models.base import Message
from src.models.router import model_router
from src.models.prompt_cache import StreamingBuffer

logger = logging.getLogger(__name__)

# 早期终止触发词
EARLY_TERMINATION_TRIGGERS = [
    "I don't know", "I cannot", "I'm sorry", "as an AI",
    "I apologize", "I'm unable", "I won't be able",
]


class StreamingOrchestrator:
    """流式编排器 —— 支持并行 LLM 流 + 早期终止 + 渐进式输出"""

    def __init__(self):
        self.buffer = StreamingBuffer(
            max_buffer=256,
            early_termination_triggers=EARLY_TERMINATION_TRIGGERS,
        )

    async def stream_and_collect(
        self,
        messages: list[Message],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        on_chunk: Callable[[str], Awaitable[None]] | None = None,
    ) -> str:
        """流式调用并收集完整响应"""
        full_response = ""

        async for chunk in model_router.chat_stream(
            messages=messages, model=model, temperature=temperature, max_tokens=max_tokens
        ):
            full_response += chunk

            if on_chunk:
                await on_chunk(chunk)

            # 早期终止检查
            self.buffer.add(chunk)
            if self.buffer.should_terminate():
                logger.info("Early termination triggered")
                break

        return full_response

    async def stream_with_tool_interleave(
        self,
        messages: list[Message],
        model: str | None = None,
        tool_executor: Callable[[str, dict[str, Any]], Awaitable[Any]] | None = None,
    ) -> AsyncIterator[dict]:
        """流式生成 + 工具调用交错执行"""
        # 简化的交叠实现：流式读完，解析工具调用，并行执行
        full_response = ""
        async for chunk in model_router.chat_stream(messages=messages, model=model):
            full_response += chunk
            yield {"type": "text", "content": chunk}

        # 解析工具调用
        if tool_executor and "tool_call" in full_response.lower():
            try:
                data = json.loads(full_response)
                if "tool_calls" in data:
                    for tc in data["tool_calls"]:
                        result = await tool_executor(tc["name"], tc["arguments"])
                        yield {"type": "tool_result", "name": tc["name"], "result": result}
            except (json.JSONDecodeError, KeyError, TypeError):
                pass

    async def parallel_streams(
        self,
        tasks: list[tuple[list[Message], str]],
        model: str | None = None,
    ) -> list[str]:
        """并行多个流式调用"""
        async def run(messages: list[Message]) -> str:
            result = ""
            async for chunk in model_router.chat_stream(messages=messages, model=model):
                result += chunk
            return result

        coros = [run(msg) for msg, _ in tasks]
        return await asyncio.gather(*coros)
