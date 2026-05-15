"""统一模型接口抽象"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ModelResponse:
    content: str
    model: str
    tokens_used: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: float = 0
    cost_usd: float = 0.0
    finish_reason: str = "stop"
    raw: Any = None
    metadata: dict = field(default_factory=dict)


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class Message:
    role: str  # system | user | assistant | tool
    content: str
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None
    name: str | None = None


class ModelProvider(ABC):
    """模型提供者抽象基类"""

    provider_name: str = ""

    @abstractmethod
    async def chat(
        self,
        messages: list[Message],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: list[dict] | None = None,
        response_format: dict | None = None,
    ) -> ModelResponse:
        ...

    @abstractmethod
    async def chat_stream(
        self,
        messages: list[Message],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: list[dict] | None = None,
    ):
        """流式返回 (async generator)"""
        ...

    @abstractmethod
    async def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        ...

    @abstractmethod
    def get_model_list(self) -> list[str]:
        ...

    def is_available(self) -> bool:
        return True

    def estimate_cost(self, model: str, prompt_tokens: int, completion_tokens: int) -> float:
        return 0.0
