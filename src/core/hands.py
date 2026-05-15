"""双手 (Hands) —— 沙盒执行环境"""

import asyncio
import json
import logging
from typing import Any

from src.tools.registry import ToolRegistry
from src.tools.idempotency import IdempotencyGuard
from src.tools.harness import ToolHarness

logger = logging.getLogger(__name__)


class HandResult:
    def __init__(self, success: bool, data: Any = None, error: str = "", tool_name: str = "", latency_ms: float = 0):
        self.success = success
        self.data = data
        self.error = error
        self.tool_name = tool_name
        self.latency_ms = latency_ms

    def to_observation(self) -> str:
        if self.success:
            return json.dumps(self.data, ensure_ascii=False)
        return f"工具执行失败: {self.error}"


class Hands:
    """执行引擎 —— 调用工具、获取结果"""

    def __init__(self, registry: ToolRegistry, idempotency: IdempotencyGuard | None = None):
        self.registry = registry
        self.idempotency = idempotency or IdempotencyGuard()
        self._harness = ToolHarness(guardrail_name="hands", registry=registry)

    async def execute(
        self,
        tool_name: str,
        arguments: dict,
        idempotency_key: str | None = None,
        session_id: str | None = None,
    ) -> HandResult:
        """执行工具调用"""
        import time
        start = time.monotonic()

        # 幂等检查
        if idempotency_key:
            cached = await self.idempotency.check(idempotency_key)
            if cached:
                logger.info(f"Idempotent hit for {idempotency_key}")
                return HandResult(success=True, data=cached, tool_name=tool_name)

            await self.idempotency.mark_in_progress(idempotency_key)

        try:
            result = await self._harness.execute(tool_name, arguments)
            if not result.get("success"):
                error = str(result.get("error") or "工具执行失败")
                latency = (time.monotonic() - start) * 1000
                logger.error(f"Tool {tool_name} failed: {error}")
                if idempotency_key:
                    await self.idempotency.clear(idempotency_key)
                return HandResult(success=False, error=error, tool_name=tool_name, latency_ms=latency)

            latency = (time.monotonic() - start) * 1000
            logger.info(f"Tool {tool_name} completed in {latency:.0f}ms")

            # 缓存幂等结果
            if idempotency_key:
                await self.idempotency.store(idempotency_key, result["data"])

            return HandResult(success=True, data=result["data"], tool_name=tool_name, latency_ms=latency)

        except asyncio.TimeoutError:
            latency = (time.monotonic() - start) * 1000
            error = "Tool timed out"
            logger.error(f"Tool {tool_name}: {error}")
            if idempotency_key:
                await self.idempotency.clear(idempotency_key)
            return HandResult(success=False, error=error, tool_name=tool_name, latency_ms=latency)
        except Exception as e:
            latency = (time.monotonic() - start) * 1000
            error = str(e)
            logger.error(f"Tool {tool_name} failed: {error}")
            if idempotency_key:
                await self.idempotency.clear(idempotency_key)
            return HandResult(success=False, error=error, tool_name=tool_name, latency_ms=latency)

    async def execute_batch(
        self,
        calls: list[tuple[str, dict, str | None]],
    ) -> list[HandResult]:
        """并行执行多个工具调用"""
        tasks = [self.execute(tool, args, ik) for tool, args, ik in calls]
        return await asyncio.gather(*tasks)
