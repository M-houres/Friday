"""Simple evaluation harness for comparing model / prompt / skill outputs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable


@dataclass
class EvalCase:
    case_id: str
    task: str
    expected: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None


@dataclass
class EvalResult:
    case_id: str
    success: bool
    score: float
    output: dict[str, Any]
    reason: str = ""


class EvalRunner:
    async def run_cases(
        self,
        cases: list[EvalCase],
        executor: Callable[[EvalCase], Awaitable[dict[str, Any]]],
        scorer: Callable[[EvalCase, dict[str, Any]], EvalResult],
    ) -> list[EvalResult]:
        results: list[EvalResult] = []
        for case in cases:
            output = await executor(case)
            results.append(scorer(case, output))
        return results
