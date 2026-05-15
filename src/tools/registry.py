"""工具注册中心 —— 注册、发现、懒加载"""

import asyncio
import importlib
import logging
from typing import Any, Callable

from src.config import settings

logger = logging.getLogger(__name__)


class ToolDefinition:
    def __init__(
        self,
        name: str,
        description: str,
        parameters: dict,
        handler: Callable,
        is_expensive: bool = False,
        requires_approval: bool = False,
        timeout_ms: int | None = None,
    ):
        self.name = name
        self.description = description
        self.parameters = parameters
        self.handler = handler
        self.is_expensive = is_expensive
        self.requires_approval = requires_approval
        self.timeout_ms = timeout_ms or settings.default_tool_timeout_ms

    def to_openai_schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }


class ToolRegistry:
    """工具注册中心"""

    def __init__(self):
        self._definitions: dict[str, ToolDefinition] = {}
        self._loaded = False

    def _ensure_loaded(self):
        if self._loaded:
            return
        importlib.import_module("src.tools.generic_tools")
        self._loaded = True

    def register(self, definition: ToolDefinition):
        self._definitions[definition.name] = definition
        logger.info(f"Tool registered: {definition.name}")

    def register_func(
        self,
        name: str,
        description: str,
        parameters: dict,
        is_expensive: bool = False,
        requires_approval: bool = False,
        timeout_ms: int | None = None,
    ):
        """装饰器工厂"""
        def decorator(fn: Callable):
            self.register(ToolDefinition(
                name=name, description=description, parameters=parameters,
                handler=fn, is_expensive=is_expensive,
                requires_approval=requires_approval, timeout_ms=timeout_ms,
            ))
            return fn
        return decorator

    def get_handler(self, name: str) -> Callable | None:
        self._ensure_loaded()
        definition = self._definitions.get(name)
        return definition.handler if definition else None

    def get_definition(self, name: str) -> ToolDefinition | None:
        self._ensure_loaded()
        return self._definitions.get(name)

    def get_schemas(self, names: list[str] | None = None) -> list[dict]:
        self._ensure_loaded()
        names = names or list(self._definitions.keys())
        return [self._definitions[n].to_openai_schema() for n in names if n in self._definitions]

    def list_tools(self) -> list[str]:
        self._ensure_loaded()
        return list(self._definitions.keys())

    def is_registered(self, name: str) -> bool:
        self._ensure_loaded()
        return name in self._definitions

    def get_timeout_ms(self, name: str) -> int:
        self._ensure_loaded()
        definition = self._definitions.get(name)
        return definition.timeout_ms if definition else settings.default_tool_timeout_ms


# 全局工具注册中心
tool_registry = ToolRegistry()


def tool(name: str, description: str, parameters: dict, **kwargs):
    """快捷装饰器: @tool(...)"""
    return tool_registry.register_func(name, description, parameters, **kwargs)
