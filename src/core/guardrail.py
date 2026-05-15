"""护栏 (Guardrail) —— 输入/输出校验"""

import json
import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

logger = logging.getLogger(__name__)


class GuardrailDecision(str, Enum):
    ALLOW = "allow"
    BLOCK = "block"
    FLAG = "flag"


@dataclass
class GuardrailResult:
    decision: GuardrailDecision
    reason: str = ""
    modified_content: str | dict | None = None
    flags: list[str] | None = None


class Guardrail:
    """安全护栏 —— 输入输出校验"""

    def __init__(self, max_output_length: int = 100000):
        self.max_output_length = max_output_length
        self._input_checks: list[Callable[..., GuardrailResult]] = []
        self._output_checks: list[Callable[..., GuardrailResult]] = []
        self._register_builtin_checks()

    def _register_builtin_checks(self):
        self._input_checks = [
            self._check_input_length,
            self._check_sensitive_keywords,
        ]
        self._output_checks = [
            self._check_output_length,
            self._check_output_format,
            self._check_blocked_content,
        ]

    def add_input_check(self, check_fn):
        self._input_checks.append(check_fn)

    def add_output_check(self, check_fn):
        self._output_checks.append(check_fn)

    def validate_input(self, tool_name: str, arguments: dict) -> GuardrailResult:
        """校验工具调用输入"""
        for check in self._input_checks:
            result = check(tool_name, arguments)
            if result.decision == GuardrailDecision.BLOCK:
                logger.warning(f"Input blocked for {tool_name}: {result.reason}")
                return result
        return GuardrailResult(decision=GuardrailDecision.ALLOW)

    def validate_output(self, tool_name: str, result: Any) -> GuardrailResult:
        """校验工具执行输出"""
        for check in self._output_checks:
            guard_result = check(tool_name, result)
            if guard_result.decision == GuardrailDecision.BLOCK:
                logger.warning(f"Output blocked for {tool_name}: {guard_result.reason}")
                return guard_result
        return GuardrailResult(decision=GuardrailDecision.ALLOW)

    # ── 内置检查 ──

    def _check_input_length(self, tool_name: str, arguments: dict) -> GuardrailResult:
        arg_str = json.dumps(arguments, ensure_ascii=False)
        if len(arg_str) > 1_000_000:
            return GuardrailResult(decision=GuardrailDecision.BLOCK, reason="Input too large (>1MB)")
        return GuardrailResult(decision=GuardrailDecision.ALLOW)

    def _check_sensitive_keywords(self, tool_name: str, arguments: dict) -> GuardrailResult:
        arg_str = json.dumps(arguments, ensure_ascii=False).lower()
        sensitive = ["password", "secret_key", "api_key", "token", "credential"]
        found = [k for k in sensitive if k in arg_str]
        if found:
            return GuardrailResult(
                decision=GuardrailDecision.FLAG,
                reason=f"Sensitive keywords found: {found}",
                flags=found,
            )
        return GuardrailResult(decision=GuardrailDecision.ALLOW)

    def _check_output_length(self, tool_name: str, result: Any) -> GuardrailResult:
        result_str = json.dumps(result, ensure_ascii=False) if not isinstance(result, str) else result
        if len(result_str) > self.max_output_length:
            return GuardrailResult(decision=GuardrailDecision.FLAG, reason=f"Output exceeds {self.max_output_length} chars")
        return GuardrailResult(decision=GuardrailDecision.ALLOW)

    def _check_output_format(self, tool_name: str, result: Any) -> GuardrailResult:
        """检查输出是否包含恶意格式"""
        if isinstance(result, str) and re.search(r"<script|<iframe|javascript:", result, re.IGNORECASE):
            return GuardrailResult(decision=GuardrailDecision.BLOCK, reason="Potential XSS detected")
        return GuardrailResult(decision=GuardrailDecision.ALLOW)

    def _check_blocked_content(self, tool_name: str, result: Any) -> GuardrailResult:
        """关键词过滤"""
        result_str = json.dumps(result, ensure_ascii=False).lower() if not isinstance(result, str) else result.lower()
        blocked = ["execute_system_command", "rm -rf", "del /f", "format c:"]
        for kw in blocked:
            if kw in result_str:
                return GuardrailResult(decision=GuardrailDecision.BLOCK, reason=f"Blocked content: {kw}")
        return GuardrailResult(decision=GuardrailDecision.ALLOW)
