"""AgentRuntime —— 每个 DAG 节点启动一个真正的 Agent 实例

Agent = Brain(思考) + Hands(工具) + Guardrail(护栏) + Tools(工具集)

这是跟原始 Anthropic Managed Agents 架构对齐的关键模块：
  脑手分离 → Agent 有独立的大脑和双手
  编排层   → Coordinator 派活给 Agent 实例
  session  → 每个 Agent 运行自有 session
  记忆库   → Agent 无状态，随时重建
"""

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from src.models.base import Message
from src.core.brain import Brain, Thought, ActionType
from src.core.guardrail_chain import GuardrailChain
from src.tools.harness import ToolHarness

logger = logging.getLogger(__name__)


class AgentState(str, Enum):
    IDLE = "idle"
    THINKING = "thinking"
    ACTING = "acting"
    OBSERVING = "observing"
    DONE = "done"
    ERROR = "error"


@dataclass
class AgentStep:
    step_index: int
    thought: str = ""
    action: str = ""         # tool_name or "respond"
    action_input: dict = field(default_factory=dict)
    observation: str = ""
    tokens_used: int = 0
    latency_ms: float = 0
    error: str = ""


@dataclass
class AgentResult:
    success: bool
    content: str = ""
    steps: list[AgentStep] = field(default_factory=list)
    tokens_used: int = 0
    total_latency_ms: float = 0
    degradation_level: int = 0
    error: str = ""


class AgentRuntime:
    """Agent 运行时 —— 真正的 Agent 实例，复用 Brain 做推理"""

    def __init__(
        self,
        name: str = "agent",
        system_prompt: str = "",
        model: str = "deepseek-chat",
        tools: list[dict] | None = None,
        tool_handlers: dict[str, Callable] | None = None,
        max_steps: int = 10,
        max_thinking_tokens: int = 2048,
        temperature: float = 0.3,
    ):
        self.id = str(uuid.uuid4())[:8]
        self.name = name
        self.system_prompt = system_prompt
        self.model = model
        self.tool_schemas = tools or []
        self.tool_handlers = tool_handlers or {}
        self.max_steps = max_steps
        self.max_thinking_tokens = max_thinking_tokens
        self.temperature = temperature

        self.state = AgentState.IDLE
        self._steps: list[AgentStep] = []
        self._guardrail = GuardrailChain(name)
        self._tool_harness = ToolHarness(guardrail_name=name, tool_handlers=self.tool_handlers)
        self._brain = Brain(
            system_prompt=system_prompt,
            model=model,
            tools=self.tool_schemas,
        )

    async def run(self, task: str, context: dict | None = None) -> AgentResult:
        """执行 Agent 的 ReAct 循环: Think → Act → Observe → Think → ..."""
        start_time = time.time()
        total_tokens = 0

        self._brain.reset()
        if self.system_prompt:
            self._brain.history.append(Message(role="system", content=self.system_prompt))
        if context:
            ctx_str = json.dumps(self._extract_relevant_context(context), ensure_ascii=False)
            self._brain.history.append(Message(role="system", content=f"上下文:\n{ctx_str}"))

        for step_idx in range(self.max_steps):
            step = AgentStep(step_index=step_idx)
            step_start = time.time()

            try:
                self.state = AgentState.THINKING

                observation = self._build_react_prompt(task)
                thought = await self._brain.think(observation=observation)

                if thought is None:
                    step.error = "模型返回空"
                    self._steps.append(step)
                    break

                step.thought = thought.reasoning[:200]
                total_tokens += thought.metadata.get("tokens_used", 0)
                step.tokens_used = thought.metadata.get("tokens_used", 0)

                if thought.action == ActionType.RESPOND:
                    step.action = "respond"
                    step.observation = thought.content
                    step.latency_ms = (time.time() - step_start) * 1000
                    self._steps.append(step)
                    self.state = AgentState.DONE
                    break

                elif thought.action == ActionType.CALL_TOOL and thought.tool_call:
                    tool_name = thought.tool_call.name
                    tool_args = thought.tool_call.arguments

                    guard_result = self._guardrail.validate_input(tool_name, tool_args)
                    if not guard_result.passed:
                        step.error = f"护栏拦截: {guard_result.reason}"
                        self._steps.append(step)
                        continue

                    self.state = AgentState.ACTING
                    step.action = tool_name
                    step.action_input = tool_args

                    tool_result = await self._act(tool_name, tool_args)

                    guard_result = self._guardrail.validate_output(tool_name, tool_result)
                    if not guard_result.passed:
                        step.observation = f"工具输出被护栏标记: {guard_result.reason}"
                    else:
                        step.observation = json.dumps(tool_result, ensure_ascii=False)[:500]

                    step.latency_ms = (time.time() - step_start) * 1000
                    self._steps.append(step)

                    self.state = AgentState.OBSERVING
                    observation_text = (
                        f"工具 {tool_name} 返回:\n"
                        f"{json.dumps(tool_result, ensure_ascii=False, default=str)[:1000]}"
                    )
                    self._brain.history.append(
                        Message(role="tool", content=observation_text, name=tool_name)
                    )

                else:
                    step.error = f"未知行动类型: {thought.action}"
                    self._steps.append(step)
                    break

            except Exception as e:
                step.error = str(e)
                step.latency_ms = (time.time() - step_start) * 1000
                self._steps.append(step)
                logger.error(f"Agent {self.name} step {step_idx} error: {e}")
                if step_idx == 0:
                    self.state = AgentState.ERROR
                    return AgentResult(
                        success=False,
                        error=str(e),
                        steps=self._steps,
                        tokens_used=total_tokens,
                        total_latency_ms=(time.time() - start_time) * 1000,
                    )
                continue

        self.state = AgentState.DONE
        total_latency = (time.time() - start_time) * 1000

        final_content = ""
        for step in reversed(self._steps):
            if step.action == "respond" and step.observation:
                final_content = step.observation
                break
        if not final_content and self._steps:
            final_content = self._steps[-1].observation

        return AgentResult(
            success=True,
            content=final_content,
            steps=self._steps,
            tokens_used=total_tokens,
            total_latency_ms=total_latency,
        )

    def _build_react_prompt(self, task: str) -> str:
        """构建 ReAct 观察提示"""
        tools_desc = ""
        if self.tool_schemas:
            tools_desc = "\n可用工具:\n" + json.dumps(self.tool_schemas, ensure_ascii=False, indent=2)

        return f"""你需要完成以下任务:
{task}
{tools_desc}

请决定下一步行动。返回 JSON:
{{
  "reasoning": "你的思考过程",
  "action": "call_tool 或 respond",
  "tool_name": "工具名(仅call_tool时)",
  "tool_args": {{}},
  "content": "回复内容(仅respond时)"
}}

规则:
- 能用工具就调工具，不要凭空编造
- 工具返回后你可以继续调其他工具或给出最终答案
- 给出最终答案时 action 用 respond"""

    async def _act(self, tool_name: str, args: dict) -> dict:
        """Act 阶段 —— 执行工具"""
        return await self._tool_harness.execute(tool_name, args)

    def _extract_relevant_context(self, context: dict) -> dict:
        """从上下文中提取 Agent 需要的部分"""
        relevant = {}
        for key in ("task", "user_preferences", "past_experiences", "best_practices", "constraints"):
            if key in context:
                relevant[key] = context[key]
        if "state" in context:
            state = context["state"]
            if isinstance(state, dict):
                relevant["state"] = {k: v for k, v in state.items() if not k.startswith("_")}
        return relevant

    def reset(self):
        """重置 Agent 状态"""
        self.state = AgentState.IDLE
        self._steps = []
        self._guardrail.reset()
        self._brain.reset()
