"""大脑 (Brain) —— 模型推理与决策"""

import json
import logging
from dataclasses import dataclass, field
from enum import Enum

from src.models.base import Message, ModelResponse
from src.models.router import model_router
from src.config import settings

logger = logging.getLogger(__name__)


class ActionType(str, Enum):
    CALL_TOOL = "call_tool"
    RESPOND = "respond"
    DELEGATE = "delegate"
    WAIT = "wait"


@dataclass
class ToolCallAction:
    name: str
    arguments: dict
    call_id: str = ""


@dataclass
class Thought:
    reasoning: str = ""
    action: ActionType = ActionType.RESPOND
    tool_call: ToolCallAction | None = None
    content: str = ""
    target_agent: str | None = None
    delegate_task: str | None = None
    confidence: float = 1.0
    metadata: dict = field(default_factory=dict)


class Brain:
    """模型推理引擎 —— 思考、决策、规划"""

    def __init__(self, system_prompt: str = "", model: str | None = None, tools: list[dict] | None = None):
        self.system_prompt = system_prompt
        self.model = model or settings.default_model
        self.tools = tools or []
        self.history: list[Message] = []

    async def think(self, observation: str = "", context: dict | None = None) -> Thought:
        """基于当前上下文和观察，生成下一步决策"""
        # 构建消息
        messages = self._build_messages(observation, context)
        self.history = messages[:]

        # 调用模型
        response = await model_router.chat(
            messages=messages,
            model=self.model,
            temperature=0.3,
            tools=self.tools if self.tools else None,
            response_format=None,
        )

        self.history.append(Message(role="assistant", content=response.content))

        # 解析决策
        return self._parse_thought(response)

    async def think_stream(self, observation: str = "", context: dict | None = None):
        """流式思考"""
        messages = self._build_messages(observation, context)

        full_response = ""
        async for chunk in model_router.chat_stream(
            messages=messages, model=self.model, temperature=0.3
        ):
            full_response += chunk
            yield chunk

        self.history.append(Message(role="assistant", content=full_response))

    def _build_messages(self, observation: str, context: dict | None) -> list[Message]:
        messages = []
        if self.system_prompt:
            messages.append(Message(role="system", content=self.system_prompt))

        if context:
            context_str = json.dumps(context, ensure_ascii=False, indent=2)
            messages.append(Message(role="system", content=f"当前上下文:\n{context_str}"))

        # 添加历史（最近 N 轮）
        messages.extend(self.history[-20:])

        if observation:
            messages.append(Message(role="user", content=observation))

        return messages

    def _parse_thought(self, response: ModelResponse) -> Thought:
        content = response.content.strip()

        # 尝试解析为结构化 JSON
        try:
            if content.startswith("{"):
                data = json.loads(content)
                return Thought(
                    reasoning=data.get("reasoning", ""),
                    action=ActionType(data.get("action", "respond")),
                    tool_call=ToolCallAction(
                        name=data["tool_call"]["name"],
                        arguments=data["tool_call"]["arguments"],
                        call_id=data["tool_call"].get("call_id", ""),
                    ) if data.get("tool_call") else None,
                    content=data.get("content", ""),
                    target_agent=data.get("target_agent"),
                    delegate_task=data.get("delegate_task"),
                    confidence=data.get("confidence", 1.0),
                    metadata={
                        "tokens_used": response.tokens_used,
                        "model": response.model,
                        "latency_ms": response.latency_ms,
                        "cost_usd": response.cost_usd,
                        "finish_reason": response.finish_reason,
                    },
                )
        except (json.JSONDecodeError, KeyError, ValueError):
            pass

        # 默认：当作文本回复
        return Thought(
            reasoning=content[:200],
            action=ActionType.RESPOND,
            content=content,
            metadata={
                "tokens_used": response.tokens_used,
                "model": response.model,
                "latency_ms": response.latency_ms,
                "cost_usd": response.cost_usd,
                "finish_reason": response.finish_reason,
            },
        )

    def reset(self):
        self.history.clear()
