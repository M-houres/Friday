"""交错执行 —— 工具调用与模型生成并行，隐藏工具延迟"""

import asyncio
import json
import logging
from typing import AsyncIterator, Callable

from src.tools.harness import ToolHarness

logger = logging.getLogger(__name__)


class InterleavedExecutor:
    """交错执行器 —— 模型生成工具调用的同时，启动工具执行"""

    def __init__(self):
        self._pending_tools: dict[str, asyncio.Task] = {}
        self._tool_results: dict[str, dict] = {}

    async def execute_interleaved(
        self,
        stream: AsyncIterator[str],
        tool_registry,
        idempotency_guard,
    ) -> AsyncIterator[dict]:
        """流式生成 + 即时工具执行

        传统: [模型生成工具1,2,3 _______] [执行1 ___] [执行2 ___] [执行3 ___]
        交错: [生成1 _] [生成2 _] [生成3 _]
               [执行1 ___]
               [       执行2 ___]
               [              执行3 ___]
        """
        full_text = ""
        tool_calls_buffer: list[dict] = []
        current_tool = None

        async for chunk in stream:
            full_text += chunk
            yield {"type": "text", "content": chunk}

            # 检测工具调用（简化：解析流中的 tool_call 标记）
            # 生产环境使用原生 function calling 的流式回调
            if '"tool":' in full_text or '"name":' in full_text:
                if current_tool is None:
                    current_tool = {"name": "", "arguments": {}}

            # 尝试提取完整的工具调用
            tool_def = self._try_extract_tool_call(full_text)
            if tool_def:
                tool_calls_buffer.append(tool_def)
                # 立即开始执行
                task = asyncio.create_task(self._run_tool(
                    tool_def["name"], tool_def.get("arguments", {}),
                    tool_registry, idempotency_guard,
                ))
                self._pending_tools[tool_def["name"]] = task
                current_tool = None

        # 等待所有工具执行完成
        for name, task in self._pending_tools.items():
            try:
                result = await task
                self._tool_results[name] = result
                yield {"type": "tool_result", "name": name, "result": result}
            except Exception as e:
                logger.error(f"Interleaved tool {name} failed: {e}")
                self._tool_results[name] = {"error": str(e)}
                yield {"type": "tool_error", "name": name, "error": str(e)}

    async def _run_tool(self, tool_name: str, args: dict, registry, idempotency) -> dict:
        """执行工具"""
        harness = ToolHarness(guardrail_name="interleaved")
        return await harness.execute(tool_name, args)

    def _try_extract_tool_call(self, text: str) -> dict | None:
        """尝试从文本中提取工具调用"""
        import re
        patterns = [
            r'"tool"\s*:\s*"(\w+)"',
            r'"name"\s*:\s*"(\w+)"',
            r'调用工具\s*[:：]\s*(\w+)',
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return {"name": match.group(1), "arguments": {}}
        return None

    def get_pending_count(self) -> int:
        return len(self._pending_tools)


class SpeculativeExecutor:
    """投机执行 —— 快慢模型赛跑，取先完成的合法结果"""

    def __init__(self, fast_models: list[str] | None = None, slow_models: list[str] | None = None):
        self.fast_models = fast_models or ["deepseek-chat", "gpt-4o-mini"]
        self.slow_models = slow_models or ["gpt-4o", "claude-sonnet-4-20250514"]

    async def speculative_generate(
        self,
        messages: list,
        validator: Callable,
        timeout_fast_s: float = 5.0,
        timeout_slow_s: float = 30.0,
    ) -> dict:
        """快慢模型赛跑 —— 返回第一个通过验证的结果"""

        from src.models.base import Message
        from src.models.router import model_router

        async def try_model(model: str) -> dict | None:
            try:
                response = await model_router.chat(
                    messages=messages, model=model, temperature=0.3,
                )
                result = {"content": response.content, "model": model, "tokens": response.tokens_used}
                if validator(result):
                    return result
            except Exception as e:
                logger.debug(f"Speculative model {model} failed: {e}")
            return None

        # 快模型先跑
        fast_tasks = [asyncio.create_task(try_model(m)) for m in self.fast_models[:2]]
        slow_tasks = [asyncio.create_task(try_model(m)) for m in self.slow_models[:1]]

        # 等待快模型
        try:
            for coro in asyncio.as_completed(fast_tasks, timeout=timeout_fast_s):
                result = await coro
                if result is not None:
                    for t in slow_tasks:
                        t.cancel()
                    logger.info(f"Speculative HIT: {result['model']}")
                    return result
        except asyncio.TimeoutError:
            pass

        # 快模型全部失败/超时 → 等慢模型
        for t in fast_tasks:
            t.cancel()

        try:
            for coro in asyncio.as_completed(slow_tasks, timeout=timeout_slow_s):
                result = await coro
                if result is not None:
                    logger.info(f"Speculative fallback: {result['model']}")
                    return result
        except asyncio.TimeoutError:
            pass

        raise RuntimeError("All speculative models failed")
