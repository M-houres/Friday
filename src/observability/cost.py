"""成本追踪 —— 按模型/Agent/任务统计 token 消耗和费用"""

import json
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class CostRecord:
    model: str
    provider: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0
    timestamp: float = field(default_factory=time.time)


class CostTracker:
    """成本追踪器"""

    def __init__(self):
        self._records: list[CostRecord] = []
        self._by_model: dict[str, list[CostRecord]] = defaultdict(list)
        self._by_agent: dict[str, list[CostRecord]] = defaultdict(list)
        self._by_task: dict[str, list[CostRecord]] = defaultdict(list)

    def record(
        self,
        model: str,
        provider: str,
        prompt_tokens: int,
        completion_tokens: int,
        cost_usd: float,
        latency_ms: float = 0,
        agent_id: str = "",
        task_id: str = "",
    ):
        record = CostRecord(
            model=model, provider=provider,
            prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
            cost_usd=cost_usd, latency_ms=latency_ms,
        )
        self._records.append(record)
        self._by_model[model].append(record)
        if agent_id:
            self._by_agent[agent_id].append(record)
        if task_id:
            self._by_task[task_id].append(record)

    def total_cost(self) -> float:
        return sum(r.cost_usd for r in self._records)

    def total_tokens(self) -> dict:
        prompt = sum(r.prompt_tokens for r in self._records)
        completion = sum(r.completion_tokens for r in self._records)
        return {"prompt": prompt, "completion": completion, "total": prompt + completion}

    def cost_by_model(self) -> dict:
        return {model: sum(r.cost_usd for r in records) for model, records in self._by_model.items()}

    def cost_by_provider(self) -> dict:
        by_provider = defaultdict(float)
        for r in self._records:
            by_provider[r.provider] += r.cost_usd
        return dict(by_provider)

    def cost_by_agent(self) -> dict:
        return {agent: sum(r.cost_usd for r in records) for agent, records in self._by_agent.items()}

    def stats(self) -> dict:
        tokens = self.total_tokens()
        return {
            "total_cost_usd": round(self.total_cost(), 6),
            "total_tokens": tokens,
            "total_calls": len(self._records),
            "avg_latency_ms": round(sum(r.latency_ms for r in self._records) / max(len(self._records), 1), 1),
            "by_model": {
                model: {
                    "cost": round(sum(r.cost_usd for r in records), 6),
                    "calls": len(records),
                    "tokens": sum(r.prompt_tokens + r.completion_tokens for r in records),
                }
                for model, records in self._by_model.items()
            },
            "by_provider": self.cost_by_provider(),
        }

    def reset(self):
        self._records.clear()
        self._by_model.clear()
        self._by_agent.clear()
        self._by_task.clear()


cost_tracker = CostTracker()
