"""模型分级调度 —— 自动判断任务复杂度，选择最优模型"""

import logging
from dataclasses import dataclass
from enum import Enum

from src.config import settings

logger = logging.getLogger(__name__)


class Complexity(int, Enum):
    TRIVIAL = 1    # 简单分类/摘要
    SIMPLE = 2     # 单步工具调用
    MODERATE = 3   # 多步推理
    COMPLEX = 4    # 复杂分析/生成
    EXPERT = 5     # 架构设计/深度推理


@dataclass
class ModelTier:
    name: str
    models: list[str]
    max_complexity: Complexity
    cost_multiplier: float = 1.0
    latency_multiplier: float = 1.0


# 模型分级表
TIERS = {
    Complexity.TRIVIAL: ModelTier("cheap", ["deepseek-chat", "gpt-4o-mini", "claude-haiku-4-5"], Complexity.TRIVIAL, 0.1, 0.3),
    Complexity.SIMPLE: ModelTier("cheap", ["deepseek-chat", "gpt-4o-mini"], Complexity.SIMPLE, 0.15, 0.4),
    Complexity.MODERATE: ModelTier("standard", ["deepseek-chat", "gpt-4o", "claude-sonnet-4-20250514"], Complexity.MODERATE, 1.0, 1.0),
    Complexity.COMPLEX: ModelTier("standard", ["gpt-4o", "claude-sonnet-4-20250514", "deepseek-reasoner"], Complexity.COMPLEX, 2.0, 2.0),
    Complexity.EXPERT: ModelTier("premium", ["claude-opus-4-1", "deepseek-reasoner"], Complexity.EXPERT, 10.0, 3.0),
}

# 降级链
FALLBACK_CHAIN = [
    "claude-opus-4-1", "claude-sonnet-4-20250514", "claude-haiku-4-5",
    "gpt-4o", "gpt-4o-mini", "deepseek-reasoner", "deepseek-chat",
]


class ComplexityClassifier:
    """任务复杂度分类器 —— 用关键词+规则快速判断，避免额外LLM调用"""

    COMPLEX_KEYWORDS = [
        "架构", "设计", "重构", "优化", "安全审计", "性能分析",
        "architecture", "refactor", "optimize", "audit",
    ]
    SIMPLE_KEYWORDS = [
        "查询", "搜索", "列出", "显示", "查看", "是什么", "定义",
        "list", "show", "find", "get", "what is",
    ]
    EXPERT_KEYWORDS = [
        "从零搭建", "系统设计", "深度分析", "源码分析",
        "from scratch", "system design", "deep dive",
    ]

    @classmethod
    def classify(cls, task: str) -> Complexity:
        task_lower = task.lower()
        text_len = len(task)

        # 专家级判断
        for kw in cls.EXPERT_KEYWORDS:
            if kw in task_lower:
                return Complexity.EXPERT

        # 复杂度启发式
        if text_len > 2000:
            return Complexity.COMPLEX
        if text_len > 1000:
            return Complexity.MODERATE

        complex_count = sum(1 for kw in cls.COMPLEX_KEYWORDS if kw in task_lower)
        simple_count = sum(1 for kw in cls.SIMPLE_KEYWORDS if kw in task_lower)

        if complex_count >= 2:
            return Complexity.COMPLEX
        if complex_count >= 1:
            return Complexity.MODERATE
        if simple_count >= 1:
            return Complexity.SIMPLE

        # 默认中等
        return Complexity.MODERATE if text_len > 500 else Complexity.SIMPLE


class TieredRouter:
    """分级路由器 —— 根据复杂度选择模型"""

    def __init__(self):
        self._circuit_cache: dict[str, bool] = {}

    async def select_model(self, task: str, preferred: str | None = None) -> str:
        """为任务选择最优模型"""
        if preferred:
            return preferred

        complexity = ComplexityClassifier.classify(task)
        tier = TIERS[complexity]

        # 从对应 tier 中选择第一个可用的模型
        for model in tier.models:
            if await self._is_available(model):
                logger.debug(f"Tier {tier.name}: {model} for [{complexity.name}] task")
                return model

        # 降级遍历
        for model in FALLBACK_CHAIN:
            if await self._is_available(model):
                logger.warning(f"All tier models unavailable, degraded to {model}")
                return model

        return settings.default_model

    async def _is_available(self, model: str) -> bool:
        if model in self._circuit_cache:
            return self._circuit_cache[model]

        try:
            from src.models.circuit_breaker import breaker_registry
            from src.models.router import model_router
            provider_name, _ = model_router.get_provider(model)
            breaker = breaker_registry.get(provider_name, model)
            available = breaker.state.value != "open"
            self._circuit_cache[model] = available
            return available
        except Exception:
            return True

    def clear_cache(self):
        self._circuit_cache.clear()


tiered_router = TieredRouter()
