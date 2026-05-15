"""Agent 当工具 + 代码即动作 + 防错工具体系"""

import json
import logging
from typing import Any, Callable, Awaitable

logger = logging.getLogger(__name__)


class AgentAsTool:
    """将 Agent 封装为工具 —— 一个 Agent 可以调另一个 Agent"""

    def __init__(self, agent_name: str, agent_fn: Callable[..., Awaitable[Any]], description: str = ""):
        self.agent_name = agent_name
        self.agent_fn = agent_fn
        self.description = description or f"调用 {agent_name} 完成任务"

    def to_tool_definition(self) -> dict:
        return {
            "name": f"agent_{self.agent_name}",
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "要委托的任务描述"},
                    "context": {"type": "object", "description": "上下文信息"},
                },
                "required": ["task"],
            },
        }

    async def execute(self, task: str, context: dict | None = None) -> dict:
        """执行委托"""
        try:
            result = await self.agent_fn(task=task, context=context or {})
            return {"success": True, "data": result, "agent": self.agent_name}
        except Exception as e:
            return {"success": False, "error": str(e), "agent": self.agent_name}


class AgentToolRegistry:
    """Agent 工具注册中心"""

    def __init__(self):
        self._agents: dict[str, AgentAsTool] = {}

    def register(self, agent: AgentAsTool):
        self._agents[agent.agent_name] = agent
        logger.info(f"Agent-as-tool registered: {agent.agent_name}")

    def get(self, agent_name: str) -> AgentAsTool | None:
        return self._agents.get(agent_name)

    def list_agents(self) -> list[dict]:
        return [
            {"name": a.agent_name, "description": a.description}
            for a in self._agents.values()
        ]

    def get_tool_definitions(self) -> list[dict]:
        return [a.to_tool_definition() for a in self._agents.values()]


agent_tool_registry = AgentToolRegistry()


class CodeAsAction:
    """代码即动作 —— Agent 直接生成 Python 代码执行"""

    def __init__(self, sandbox=None):
        self.sandbox = sandbox

    async def execute_code(self, code: str, timeout_s: int = 30) -> dict:
        """执行 Agent 生成的代码"""
        if self.sandbox is None:
            from src.tools.isolated_sandbox import sandbox_pool
            sandbox = await sandbox_pool.acquire()
            try:
                result = await sandbox.run_python(code, timeout_s)
                return result
            finally:
                await sandbox_pool.release(sandbox)
        else:
            return await self.sandbox.run_python(code, timeout_s)

    @staticmethod
    def build_code_prompt(task: str, context: dict | None = None) -> str:
        """构建代码生成提示词"""
        ctx = json.dumps(context or {}, ensure_ascii=False, indent=2)
        return f"""你是 Python 专家。用代码完成任务，结果用 print(json.dumps(...)) 输出。

任务: {task}
上下文: {ctx}

规则:
1. 只输出可运行 Python 代码
2. 结果用 print(json.dumps({{"result": ...}})) 返回
3. 不要解释，不要 markdown
4. 处理异常，出错输出 {{"error": str(e)}}
"""


class PokaYokeTool:
    """防错工具体系 —— 工具自带约束，Agent 天然调不错

    原则 (来自 Anthropic):
    1. 用绝对路径，不要相对路径
    2. 参数明确，不模糊
    3. 描述写得比给人看的文档还认真
    4. 每个工具只做一件事
    """

    @staticmethod
    def create_file_tool(name: str, base_dir: str = "/workspace") -> dict:
        """创建文件操作工具定义（防错模式）"""
        return {
            "name": name,
            "description": f"""{name}: 文件操作工具

## 用途
在 {base_dir} 目录下操作文件。

## 参数
- path (string, 必填): 文件的绝对路径。必须是 {base_dir}/ 开头的完整路径。
  正确: "{base_dir}/src/main.py"
  错误: "src/main.py" (缺少前缀)
  错误: "../etc/passwd" (路径穿越)

- content (string): 文件内容

## 注解
- path 必须以 {base_dir}/ 开头，否则直接报错
- 不支持相对路径和 ../
- 大文件 (>1MB) 请使用 stream 模式
""",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": f"绝对路径，必须以 {base_dir}/ 开头",
                    },
                    "content": {"type": "string", "description": "文件内容"},
                },
                "required": ["path"],
            },
        }

    @staticmethod
    def create_search_tool() -> dict:
        """创建搜索工具定义（防错模式）"""
        return {
            "name": "search",
            "description": """search: 搜索工具

## 用途
在数据集中搜索信息。

## 参数
- query (string, 必填): 搜索关键词。不要超过 500 字符。
  正确: "Python asyncio 用法"
  错误: 空字符串
  错误: 把整个问题粘过来

- limit (integer, 默认 10): 返回结果数，范围 1-50。

## 注解
- query 要简洁，不要重复用户的问题
- 限制自动裁剪到 50
""",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词，简洁明确"},
                    "limit": {"type": "integer", "default": 10, "minimum": 1, "maximum": 50},
                },
                "required": ["query"],
            },
        }
