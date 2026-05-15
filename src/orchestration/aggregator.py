"""聚合器 (Aggregator) —— 收集节点结果，拼装最终输出"""

import json
import logging

from src.models.base import Message
from src.models.router import model_router
from src.config import settings

logger = logging.getLogger(__name__)

AGGREGATOR_SYSTEM_PROMPT = """你是一个结果聚合专家。你的工作是将多个子任务的结果整合成一份连贯、完整的最终回答。

## 规则

1. 忠实于原始结果，不要编造信息
2. 指出哪些结果是"部分完成"的
3. 如果某个子任务失败，说明原因但不影响其他结果
4. 结构清晰，可以用标题、列表等
5. 如果结果之间存在矛盾，指出矛盾处
"""


class Aggregator:
    """结果聚合器 —— 用 LLM 拼接子任务结果"""

    def __init__(self, model: str | None = None):
        self.model = model or settings.default_model
        self.system_prompt = AGGREGATOR_SYSTEM_PROMPT

    async def aggregate(
        self,
        task: str,
        results: dict[str, dict],
        failed_nodes: list[str],
        degradation_level: int = 0,
    ) -> dict:
        """聚合子任务结果"""
        if not results:
            return {"content": "未获取到任何结果", "degradation_level": degradation_level}

        # 构建结果上下文
        results_text = []
        for node_id, result in results.items():
            if "error" in result:
                results_text.append(f"[{node_id}] 失败: {result['error']}")
            else:
                result_str = json.dumps(result, ensure_ascii=False, indent=2)
                results_text.append(f"[{node_id}]\n{result_str}")

        combined = "\n\n---\n\n".join(results_text)

        messages = [
            Message(role="system", content=self.system_prompt),
            Message(
                role="user",
                content=f"原始任务:\n{task}\n\n子任务结果:\n{combined}\n\n请整合为最终输出。",
            ),
        ]

        response = await model_router.chat(
            messages=messages,
            model=self.model,
            temperature=0.5,
        )

        return {
            "content": response.content,
            "degradation_level": degradation_level,
            "failed_nodes": failed_nodes,
            "node_results": results,
            "tokens_used": response.tokens_used,
            "model": response.model,
        }
