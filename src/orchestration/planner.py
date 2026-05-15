"""计划器 (Planner) —— 分析任务，生成 DAG 计划"""

import json
import logging
import uuid

from src.models.base import Message
from src.models.router import model_router
from src.config import settings

logger = logging.getLogger(__name__)

_DEFAULT_PLANNER_PROMPT = """你是一个任务拆解专家。你的工作是将用户的复杂任务拆解为可并行执行的子任务。

## 输出格式

你必须以 JSON 格式输出，包含以下字段：

```json
{
  "reasoning": "拆解思路...",
  "nodes": [
    {
      "node_id": "唯一标识",
      "task": "子任务描述（清晰、可执行）",
      "dependencies": ["依赖的 node_id 列表"],
      "is_critical": true
    }
  ],
  "edges": [["from_node_id", "to_node_id"]]
}
```

## 规则

1. 能并行的尽量并行（减少依赖）
2. 有先后顺序的标明依赖
3. 非核心步骤标记 is_critical: false（框架可以降级跳过）
4. 每个子任务应该是 agent 可以独立完成的
5. 拆解粒度适中（3-10 个子任务）
"""


def _load_planner_prompt() -> str:
    """从配置文件加载 Planner 提示词，失败则用默认"""
    from pathlib import Path
    config_path = Path(__file__).parent.parent.parent / "config" / "planner-prompt.txt"
    try:
        if config_path.exists():
            return config_path.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return _DEFAULT_PLANNER_PROMPT


class Planner:
    """任务计划器 —— 用 LLM 将用户任务拆解为 DAG"""

    def __init__(self, model: str | None = None):
        self.model = model or settings.default_model
        self.system_prompt = _load_planner_prompt()

    async def plan(self, task: str, context: dict | None = None) -> dict:
        """拆解任务，返回 DAG 计划 —— 优先匹配 Skill"""
        matched_skill = None

        # === 新增: Skill 匹配 ===
        try:
            from src.tools.skill import skill_registry
            matches = skill_registry.find_by_trigger(task)
            if matches:
                matched_skill = matches[0]  # 取最匹配的
                logger.info(f"Skill matched: {matched_skill.name} for task: {task[:50]}")

                # 用 Skill 的 workflow 构建 DAG
                skill_instance = matched_skill()
                workflow = matched_skill.get_workflow()
                if workflow:
                    nodes = []
                    edges = []
                    for wf_node in workflow:
                        node_id = wf_node.get("id", wf_node.get("tool", "step"))
                        nodes.append({
                            "node_id": node_id,
                            "task": wf_node.get("name", wf_node.get("task", node_id)),
                            "dependencies": wf_node.get("dependencies", []),
                            "is_critical": True,
                            "skill_name": matched_skill.name,
                            "tools": [wf_node.get("tool", "")] if wf_node.get("tool") else [],
                        })
                        for dep in wf_node.get("dependencies", []):
                            edges.append([dep, node_id])

                    plan = {
                        "nodes": nodes,
                        "edges": edges,
                        "_skill": matched_skill.name,
                        "_execution_mode": "skill_pipeline",
                    }
                    plan["_raw_reasoning"] = f"Skill matched: {matched_skill.name}"
                    plan["_tokens_used"] = 0
                    plan["_model"] = "skill"
                    logger.info(f"Skill plan: {len(nodes)} nodes from {matched_skill.name}")
                    return plan
        except Exception as e:
            logger.debug(f"Skill matching skipped: {e}")

        # === 未匹配 Skill → LLM 拆解 ===
        messages = [Message(role="system", content=self.system_prompt)]

        if context:
            ctx_str = json.dumps(context, ensure_ascii=False, indent=2)
            messages.append(Message(role="system", content=f"当前上下文:\n{ctx_str}"))

        messages.append(Message(role="user", content=f"请拆解以下任务:\n{task}"))

        response = await model_router.chat(
            messages=messages,
            model=self.model,
            temperature=0.3,
            response_format={"type": "json_object"},
        )

        # 解析计划
        plan = self._parse_plan(response.content)
        plan["_raw_reasoning"] = response.content[:200]
        plan["_tokens_used"] = response.tokens_used
        plan["_model"] = response.model
        plan["_execution_mode"] = "agent_dag"

        logger.info(f"Planner generated {len(plan.get('nodes', []))} nodes for task")
        return plan

    def _parse_plan(self, content: str) -> dict:
        """解析 LLM 输出的计划 JSON"""
        try:
            content = content.strip()
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]
            return json.loads(content)
        except (json.JSONDecodeError, IndexError, KeyError) as e:
            logger.warning(f"Failed to parse planner output: {e}")
            # 兜底：单节点计划
            return {
                "nodes": [{"node_id": "task_1", "task": content[:500], "dependencies": [], "is_critical": True}],
                "edges": [],
            }
