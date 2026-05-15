"""Unified tool execution harness."""

from __future__ import annotations

import asyncio
from typing import Any, Callable

from src.core.guardrail_chain import GuardrailChain
from src.tools.registry import ToolRegistry, tool_registry


class ToolHarness:
    def __init__(
        self,
        *,
        guardrail_name: str = "tool_harness",
        tool_handlers: dict[str, Callable] | None = None,
        registry: ToolRegistry | None = None,
    ):
        self.tool_handlers = tool_handlers or {}
        self.registry = registry or tool_registry
        self.guardrail = GuardrailChain(guardrail_name)

    async def execute(self, tool_name: str, args: dict | None = None) -> dict[str, Any]:
        resolved_args = dict(args or {})
        guard_result = self.guardrail.validate_input(tool_name, resolved_args)
        if not guard_result.passed:
            return {"success": False, "error": f"护栏拦截: {guard_result.reason}"}

        pre_tool_guard = self.guardrail.validate_pre_tool(tool_name, resolved_args)
        if not pre_tool_guard.passed:
            return {"success": False, "error": f"工具执行前被护栏拦截: {pre_tool_guard.reason}"}

        handler = self.tool_handlers.get(tool_name) or self.registry.get_handler(tool_name)
        if handler is None:
            return {"success": False, "error": f"工具未找到: {tool_name}"}

        timeout_s = max(self.registry.get_timeout_ms(tool_name) / 1000.0, 0.1)
        try:
            if asyncio.iscoroutinefunction(handler):
                result = await asyncio.wait_for(handler(**resolved_args), timeout=timeout_s)
            else:
                result = await asyncio.wait_for(asyncio.to_thread(handler, **resolved_args), timeout=timeout_s)

            post_tool_guard = self.guardrail.validate_post_tool(tool_name, result)
            if not post_tool_guard.passed:
                return {"success": False, "error": f"工具输出被护栏拦截: {post_tool_guard.reason}"}
            output = {"success": True, "data": result}
        except asyncio.TimeoutError:
            output = {"success": False, "error": "Tool timed out"}
        except Exception as exc:
            output = {"success": False, "error": str(exc)}

        output_guard = self.guardrail.validate_output(tool_name, output)
        if not output_guard.passed and output.get("success") is not False:
            return {"success": False, "error": f"工具输出被护栏拦截: {output_guard.reason}"}
        return output
