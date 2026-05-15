"""模型路由器 —— 统一调用入口，熔断 + 重试 + 模型选择"""

import logging
from typing import AsyncIterator

from src.config import settings
from src.models.base import Message, ModelProvider, ModelResponse
from src.models.circuit_breaker import breaker_registry
from src.models.retry import RetryPolicy, with_retry
from src.models.deepseek import DeepSeekProvider
from src.models.openai import OpenAIProvider
from src.models.anthropic import AnthropicProvider
from src.productization.managed_config import managed_config_store

logger = logging.getLogger(__name__)


class ModelRouter:
    """统一模型调用入口"""

    def __init__(self):
        self._providers: dict[str, ModelProvider] = {}
        self._register_builtin()

    def _register_builtin(self):
        if settings.deepseek_api_key:
            self._providers["deepseek"] = DeepSeekProvider()
        if settings.openai_api_key:
            self._providers["openai"] = OpenAIProvider()
        if settings.anthropic_api_key:
            self._providers["anthropic"] = AnthropicProvider()

    def register(self, name: str, provider: ModelProvider):
        self._providers[name] = provider

    def get_provider(self, model: str) -> tuple[str, ModelProvider]:
        """根据模型名路由到对应 Provider"""
        model_lower = model.lower()
        if "deepseek" in model_lower:
            return "deepseek", self._providers["deepseek"]
        if "gpt" in model_lower or "o1" in model_lower or "o3" in model_lower:
            return "openai", self._providers["openai"]
        if "claude" in model_lower:
            if self._providers.get("anthropic") and self._providers["anthropic"].is_available():
                return "anthropic", self._providers["anthropic"]
            raise RuntimeError(f"Anthropic provider not available for model {model}")
        raise ValueError(f"Unknown model provider for: {model}")

    async def chat(
        self,
        messages: list[Message],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: list[dict] | None = None,
        response_format: dict | None = None,
    ) -> ModelResponse:
        model = model or str(managed_config_store.get_model_strategy().get("default_model") or settings.default_model)
        provider_name, provider = self.get_provider(model)
        breaker = breaker_registry.get(provider_name, model)
        policy = RetryPolicy()

        # 熔断器检查
        if not await breaker.before_call():
            # 尝试模型降级
            fallback_model = self._get_fallback_model(model)
            if fallback_model:
                logger.warning(f"Circuit open for {model}, falling back to {fallback_model}")
                return await self.chat(messages, fallback_model, temperature, max_tokens, tools, response_format)
            raise RuntimeError(f"Circuit breaker open for {provider_name}/{model}")

        async def _call():
            return await provider.chat(
                messages, model=model, temperature=temperature,
                max_tokens=max_tokens, tools=tools, response_format=response_format,
            )

        try:
            result = await with_retry(_call, policy=policy)
            await breaker.on_success()
            return result
        except Exception as e:
            error_type = type(e).__name__
            status_code = getattr(e, "status_code", getattr(e, "status", None))
            await breaker.on_failure(str(status_code) if status_code else error_type)

            # 降级到备用模型
            fallback = self._get_fallback_model(model)
            if fallback:
                logger.warning(f"Model {model} failed, falling back to {fallback}")
                return await self.chat(messages, fallback, temperature, max_tokens, tools, response_format)
            raise

    async def chat_stream(
        self,
        messages: list[Message],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: list[dict] | None = None,
    ) -> AsyncIterator[str]:
        model = model or str(managed_config_store.get_model_strategy().get("default_model") or settings.default_model)
        _, provider = self.get_provider(model)
        async for chunk in provider.chat_stream(messages, model, temperature, max_tokens, tools):
            yield chunk

    def _get_fallback_model(self, model: str) -> str | None:
        return managed_config_store.resolve_fallback(model)

    def list_providers(self) -> list[str]:
        return list(self._providers.keys())

    def get_circuit_states(self) -> list[dict]:
        return breaker_registry.all_states()

    async def close(self):
        for p in self._providers.values():
            if hasattr(p, "close"):
                await p.close()

    async def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        """生成文本 embedding —— 默认用 OpenAI"""
        from src.config import settings
        model = model or settings.memory_embedding_model

        if "openai" in self._providers and self._providers["openai"].is_available():
            return await self._providers["openai"].embed(texts, model)

        if "deepseek" in self._providers:
            try:
                return await self._providers["deepseek"].embed(texts, model)
            except NotImplementedError:
                pass

        raise RuntimeError("No embedding-capable provider available (need OpenAI API key)")


model_router = ModelRouter()
