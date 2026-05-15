"""三级护栏链 —— 输入 → 工具 → 输出 全链校验 + tripwire 熔断"""

import json
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

logger = logging.getLogger(__name__)


class GuardLevel(str, Enum):
    INPUT = "input"       # 输入校验
    TOOL = "tool"         # 工具执行前后
    OUTPUT = "output"     # 输出校验

class TripwireAction(str, Enum):
    BLOCK = "block"       # 阻止执行，抛异常
    FLAG = "flag"         # 标记，继续执行但记录
    MODIFY = "modify"     # 修改输入/输出后继续

@dataclass
class GuardResult:
    passed: bool = True
    action: TripwireAction = TripwireAction.BLOCK
    reason: str = ""
    modified: Any = None
    flags: list[str] = field(default_factory=list)


class GuardrailChain:
    """三级护栏链"""

    def __init__(self, tool_name: str = ""):
        self.tool_name = tool_name
        self._input_checks: list[Callable] = []
        self._pre_tool_checks: list[Callable] = []
        self._post_tool_checks: list[Callable] = []
        self._output_checks: list[Callable] = []
        self._trip_count: int = 0
        self._max_trips: int = 10
        self._tripped: bool = False
        self._setup_defaults()

    def _setup_defaults(self):
        # 输入默认检查
        self._input_checks = [
            self._check_injection,
            self._check_size_limit,
            self._check_sensitive_data,
        ]
        # 输出默认检查
        self._output_checks = [
            self._check_output_injection,
            self._check_output_size,
        ]
        # 工具默认检查
        self._pre_tool_checks = [
            self._check_tool_allowed,
        ]
        self._post_tool_checks = [
            self._check_tool_result_valid,
        ]

    def add_check(self, level: GuardLevel, check_fn: Callable):
        if level == GuardLevel.INPUT:
            self._input_checks.append(check_fn)
        elif level == GuardLevel.TOOL:
            self._pre_tool_checks.append(check_fn)
        elif level == GuardLevel.OUTPUT:
            self._output_checks.append(check_fn)

    def is_tripped(self) -> bool:
        return self._tripped

    def trip(self, reason: str = ""):
        self._trip_count += 1
        if self._trip_count >= self._max_trips:
            self._tripped = True
            logger.error(f"Guardrail TRIPPED for {self.tool_name}: {reason}")

    def reset(self):
        self._trip_count = 0
        self._tripped = False

    def validate_input(self, tool_name: str, arguments: dict) -> GuardResult:
        if self._tripped:
            return GuardResult(passed=False, reason="Guardrail tripped")

        for check in self._input_checks:
            result = check(tool_name, arguments)
            if not result.passed:
                if result.action == TripwireAction.BLOCK:
                    self.trip(result.reason)
                    return result
                elif result.action == TripwireAction.FLAG:
                    logger.warning(f"Guardrail FLAG (input): {result.reason}")
                elif result.action == TripwireAction.MODIFY:
                    arguments = result.modified or arguments
        return GuardResult(passed=True)

    def validate_pre_tool(self, tool_name: str, arguments: dict) -> GuardResult:
        if self._tripped:
            return GuardResult(passed=False, reason="Guardrail tripped")

        for check in self._pre_tool_checks:
            result = check(tool_name, arguments)
            if not result.passed and result.action == TripwireAction.BLOCK:
                self.trip(result.reason)
                return result
        return GuardResult(passed=True)

    def validate_post_tool(self, tool_name: str, result_data: Any) -> GuardResult:
        if self._tripped:
            return GuardResult(passed=False, reason="Guardrail tripped")

        for check in self._post_tool_checks:
            guard_result = check(tool_name, result_data)
            if not guard_result.passed and guard_result.action == TripwireAction.BLOCK:
                self.trip(guard_result.reason)
                return guard_result
        return GuardResult(passed=True)

    def validate_output(self, tool_name: str, output: Any) -> GuardResult:
        if self._tripped:
            return GuardResult(passed=False, reason="Guardrail tripped")

        for check in self._output_checks:
            result = check(tool_name, output)
            if not result.passed:
                if result.action == TripwireAction.BLOCK:
                    self.trip(result.reason)
                    return result
                elif result.action == TripwireAction.MODIFY:
                    output = result.modified or output
        return GuardResult(passed=True)

    # ── 默认检查函数 ──

    def _check_injection(self, tool_name: str, args: dict) -> GuardResult:
        arg_str = json.dumps(args, ensure_ascii=False).lower()
        patterns = [
            r"<script|<iframe|javascript:",
            r"rm\s+-rf\s+/|del\s+/f|format\s+c:",
            r"system\(|exec\(|eval\(|__import__",
        ]
        for p in patterns:
            if re.search(p, arg_str):
                return GuardResult(passed=False, reason=f"Injection pattern detected: {p}")
        return GuardResult(passed=True)

    def _check_size_limit(self, tool_name: str, args: dict) -> GuardResult:
        arg_str = json.dumps(args, ensure_ascii=False)
        if len(arg_str) > 10_000_000:  # 10MB
            return GuardResult(passed=False, reason="Input exceeds 10MB limit")
        return GuardResult(passed=True)

    def _check_sensitive_data(self, tool_name: str, args: dict) -> GuardResult:
        arg_str = json.dumps(args, ensure_ascii=False).lower()
        sensitive = ["password", "secret", "api_key", "token", "credit_card"]
        found = [k for k in sensitive if k in arg_str]
        if found:
            return GuardResult(
                passed=True,  # 不阻止，但标记
                action=TripwireAction.FLAG,
                reason=f"Sensitive keywords: {found}",
                flags=found,
            )
        return GuardResult(passed=True)

    def _check_tool_allowed(self, tool_name: str, args: dict) -> GuardResult:
        blocked_tools = ["__builtins__", "eval", "exec", "compile"]
        if tool_name.lower() in blocked_tools:
            return GuardResult(passed=False, reason=f"Tool {tool_name} is blocked")
        return GuardResult(passed=True)

    def _check_tool_result_valid(self, tool_name: str, result: Any) -> GuardResult:
        if result is None:
            return GuardResult(passed=True)
        result_str = str(result)
        if len(result_str) > 50_000_000:  # 50MB
            return GuardResult(passed=False, reason="Tool output exceeds 50MB")
        return GuardResult(passed=True)

    def _check_output_injection(self, tool_name: str, output: Any) -> GuardResult:
        output_str = str(output).lower()
        if re.search(r"<script|<iframe|javascript:", output_str):
            return GuardResult(passed=False, reason="XSS in output")
        return GuardResult(passed=True)

    def _check_output_size(self, tool_name: str, output: Any) -> GuardResult:
        if isinstance(output, (str, bytes)):
            if len(output) > 100_000:  # 100KB limit for text output
                return GuardResult(
                    passed=True,
                    action=TripwireAction.FLAG,
                    reason=f"Large output: {len(output)} chars",
                )
        return GuardResult(passed=True)


class GuardrailRegistry:
    """护栏注册中心 —— 每个工具独立护栏链"""

    def __init__(self):
        self._chains: dict[str, GuardrailChain] = {}

    def get(self, tool_name: str) -> GuardrailChain:
        if tool_name not in self._chains:
            self._chains[tool_name] = GuardrailChain(tool_name)
        return self._chains[tool_name]

    def is_tripped(self, tool_name: str) -> bool:
        chain = self._chains.get(tool_name)
        return chain.is_tripped() if chain else False

    def reset_all(self):
        for chain in self._chains.values():
            chain.reset()

    def stats(self) -> dict:
        return {
            name: {"tripped": chain.is_tripped(), "trip_count": chain._trip_count}
            for name, chain in self._chains.items()
        }


guardrail_registry = GuardrailRegistry()
