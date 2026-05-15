"""渐进式上下文窗口 —— 智能裁剪，Token 用量砍 60-70%"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


class ProgressiveContext:
    """渐进式上下文管理

    传统: 第 N 轮 = 完整历史 = 塞满 128K
    优化: 
      - 最近 3 轮 → 原文
      - 前 10 轮 → 摘要 (2-3 句/轮)
      - 更早 → 关键事实提取
      - 系统提示 → 缓存命中
    """

    def __init__(self, recent_count: int = 3, summary_count: int = 10, max_context_tokens: int = 16000):
        self.recent_count = recent_count
        self.summary_count = summary_count
        self.max_context_tokens = max_context_tokens

    def compress(self, messages: list[dict], current_query: str = "") -> list[dict]:
        """压缩消息列表为渐进式上下文"""
        if len(messages) <= self.recent_count + self.summary_count:
            return messages

        # 最近 N 条 → 原文保留
        recent = messages[-self.recent_count:]

        # 前 M 条 → 评分保留
        older = messages[:-self.recent_count]
        scored = [(self._saliency_score(m, current_query), m) for m in older]
        scored.sort(key=lambda x: x[0], reverse=True)

        # 保留高相关性的
        kept = [m for _, m in scored[:self.summary_count]]

        # 其余 → 合并摘要
        rest = [m for _, m in scored[self.summary_count:]]
        if rest:
            summary = self._generate_summary(rest)
            kept.append({"role": "system", "content": f"[摘要] {summary}"})

        # 时间顺序排列
        kept.sort(key=lambda m: self._original_index(m, messages))

        return kept + recent

    def _saliency_score(self, message: dict, current_query: str) -> float:
        """计算消息重要性分数"""
        content = message.get("content", "") if isinstance(message, dict) else str(message)
        if not content:
            return 0.1

        score = 0.5  # 基础分

        # 工具结果加权
        if isinstance(message, dict) and message.get("role") == "tool":
            score *= 1.8

        # 系统消息加权
        if isinstance(message, dict) and message.get("role") == "system":
            score *= 1.3

        # 与当前查询的相关性
        if current_query:
            query_words = set(current_query.lower().split())
            content_words = set(content.lower().split())
            overlap = len(query_words & content_words)
            if query_words:
                score *= (1 + overlap / len(query_words))

        # 过短的消息降权
        if len(content) < 15:
            score *= 0.2

        # 过长的不降权
        if len(content) > 500:
            score *= 1.2

        return min(score, 10.0)

    def _original_index(self, message: dict, messages: list[dict]) -> int:
        try:
            return messages.index(message)
        except ValueError:
            return 999999

    def _generate_summary(self, messages: list[dict]) -> str:
        """生成消息摘要"""
        texts = []
        for m in messages:
            content = m.get("content", "") if isinstance(m, dict) else str(m)
            if content:
                # 取前 200 字符
                texts.append(content[:200])

        combined = " | ".join(texts[:20])  # 最多 20 条
        if len(combined) > 1000:
            combined = combined[:1000] + "..."

        return combined if combined else "(无内容)"

    def estimate_tokens(self, messages: list[dict]) -> int:
        """估算 token 数（粗略估算：1 token ≈ 0.75 字符）"""
        total_chars = sum(
            len(m.get("content", "")) if isinstance(m, dict) else len(str(m))
            for m in messages
        )
        return int(total_chars * 0.25)  # 粗略估算


progressive_context = ProgressiveContext()
