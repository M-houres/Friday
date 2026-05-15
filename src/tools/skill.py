"""Skill 层 —— 工具 + 流程 + 输出 打包为一个可复用模块

一个 Skill = 工具集合 + 执行流程 + 输出模板 + 触发条件
用法:
    @skill(
        name="generate_ppt",
        trigger="生成PPT|做PPT|幻灯片",
        description="一句话生成专业PPT"
    )
    class PPTSkill(FridaySkill):
        ...
"""

import asyncio
import inspect
import logging
from typing import Any, Awaitable, Callable, get_type_hints

logger = logging.getLogger(__name__)


class SkillTool:
    """Skill 中的工具定义"""
    def __init__(self, name: str, fn: Callable, description: str, parameters: dict,
                 is_expensive: bool = False, requires_approval: bool = False,
                 depends_on: list[str] | None = None):
        self.name = name
        self.fn = fn
        self.description = description
        self.parameters = parameters
        self.is_expensive = is_expensive
        self.requires_approval = requires_approval
        self.depends_on = depends_on or []

    def to_schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }


class FridaySkill:
    """Skill 基类"""
    name: str = ""
    description: str = ""
    trigger: str = ""       # 触发关键词，用 | 分隔，如 "PPT|幻灯片|演示文稿"
    version: str = "1.0.0"
    icon: str = "🧩"
    metadata: dict = {}

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        cls._tools: dict[str, SkillTool] = {}
        cls._workflow: list[dict] = []
        cls._output_schema: dict = {}

        # 收集所有 @tool 装饰的方法
        for attr_name in dir(cls):
            attr = getattr(cls, attr_name)
            if callable(attr) and hasattr(attr, "_skill_tool_meta"):
                meta = attr._skill_tool_meta
                tool = SkillTool(
                    name=meta["name"],
                    fn=attr,
                    description=meta["description"],
                    parameters=meta["parameters"],
                    is_expensive=meta.get("is_expensive", False),
                    requires_approval=meta.get("requires_approval", False),
                    depends_on=meta.get("depends_on", []),
                )
                cls._tools[tool.name] = tool

    @classmethod
    def get_tools(cls) -> list[SkillTool]:
        return list(cls._tools.values())

    @classmethod
    def get_tool_schemas(cls) -> list[dict]:
        return [t.to_schema() for t in cls.get_tools()]

    @classmethod
    def get_workflow(cls) -> list[dict]:
        if hasattr(cls, "workflow") and isinstance(cls.workflow, list):
            return cls.workflow
        # 自动推断: 没有依赖的并行, 有依赖的串行
        nodes = []
        for name, tool in cls._tools.items():
            nodes.append({
                "id": name,
                "name": tool.name,
                "tool": name,
                "dependencies": getattr(tool, "depends_on", []),
            })
        return nodes

    @classmethod
    def get_output_schema(cls) -> dict:
        if hasattr(cls, "output") and cls.output:
            return cls.output
        return {"result": "any"}

    @classmethod
    def matches_trigger(cls, text: str) -> bool:
        if not cls.trigger:
            return False
        keywords = cls.trigger.split("|")
        text_lower = text.lower()
        return any(kw.strip().lower() in text_lower for kw in keywords)

    async def execute_tool(self, tool_name: str, **kwargs) -> Any:
        tool = self._tools.get(tool_name)
        if tool is None:
            raise ValueError(f"Tool not found in skill {self.name}: {tool_name}")
        result = tool.fn(self, **kwargs)
        if asyncio.iscoroutine(result):
            result = await result
        return result

    async def execute_workflow(
        self,
        task: str,
        context: dict | None = None,
        step_callback: Callable[[str, dict], Awaitable[None]] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """按 workflow 声明顺序执行，并把步骤结果累积进统一 state。"""
        workflow = self.get_workflow()
        results_by_node: dict[str, Any] = {}
        state: dict[str, Any] = dict(context or {})
        completed_ids: set[str] = set()

        if not workflow:
            return results_by_node, state

        total_steps = len(workflow)

        while len(completed_ids) < total_steps:
            ready_nodes: list[tuple[int, dict, str]] = []
            for index, node in enumerate(workflow):
                node_id = node.get("id") or node.get("tool") or f"step_{index}"
                if node_id in completed_ids:
                    continue
                deps = node.get("dependencies", [])
                if all(dep in completed_ids for dep in deps):
                    ready_nodes.append((index, node, node_id))

            if not ready_nodes:
                pending = [
                    node.get("id") or node.get("tool") or f"step_{index}"
                    for index, node in enumerate(workflow)
                    if (node.get("id") or node.get("tool") or f"step_{index}") not in completed_ids
                ]
                raise RuntimeError(f"Workflow stalled in skill {self.name}; pending nodes: {pending}")

            # 第一轮先保证语义正确，按拓扑顺序稳定执行；节点内部仍可自行并行。
            for index, node, node_id in ready_nodes:
                tool_name = node.get("tool")
                if not tool_name:
                    raise ValueError(f"Workflow node {node_id} missing tool declaration")

                payload = {
                    "skill_name": self.name,
                    "node_id": node_id,
                    "tool_name": tool_name,
                    "node": node,
                    "step_index": index,
                    "total_steps": total_steps,
                }

                if step_callback:
                    await step_callback("start", payload)

                try:
                    output = await self.execute_tool(
                        tool_name,
                        task=task,
                        context=dict(state),
                        **node.get("args", {}),
                    )
                except Exception as exc:
                    if step_callback:
                        await step_callback("error", {**payload, "error": str(exc)})
                    raise

                if isinstance(output, dict) and output.get("success") is False:
                    error_message = output.get("error", f"Skill step failed: {node_id}")
                    if step_callback:
                        await step_callback("error", {**payload, "error": error_message, "output": output})
                    raise RuntimeError(error_message)

                results_by_node[node_id] = output
                state[node_id] = output
                state[tool_name] = output
                completed_ids.add(node_id)

                if step_callback:
                    await step_callback("complete", {**payload, "output": output})

        return results_by_node, state

    async def run(self, task: str, context: dict | None = None) -> dict:
        """按 workflow 顺序执行所有工具"""
        results, _ = await self.execute_workflow(task, context=context)
        return results

    def to_dict(self) -> dict:
        metadata = dict(getattr(self, "metadata", {}) or {})
        return {
            "name": self.name,
            "description": self.description,
            "trigger": self.trigger,
            "version": self.version,
            "icon": self.icon,
            "tools": [t.to_schema() for t in self.get_tools()],
            "workflow": self.get_workflow(),
            "output": self.get_output_schema(),
            "metadata": metadata,
        }


def tool(name: str = "", description: str = "", parameters: dict | None = None,
         depends_on: list[str] | None = None, is_expensive: bool = False,
         requires_approval: bool = False):
    """Skill 工具装饰器"""
    def decorator(fn):
        fn._skill_tool_meta = {
            "name": name or fn.__name__,
            "description": description or fn.__doc__ or "",
            "parameters": parameters or _infer_parameters(fn),
            "depends_on": depends_on or [],
            "is_expensive": is_expensive,
            "requires_approval": requires_approval,
        }
        return fn
    return decorator


def _infer_parameters(fn: Callable) -> dict:
    """从函数签名推断参数 Schema"""
    hints = get_type_hints(fn)
    sig = inspect.signature(fn)
    properties = {}
    required = []
    for param_name, param in sig.parameters.items():
        if param_name in ("self", "cls", "task", "context"):
            continue
        param_type = "string"
        if param_name in hints:
            origin = getattr(hints[param_name], "__origin__", None)
            if origin is list:
                param_type = "array"
            elif hints[param_name] is int:
                param_type = "integer"
            elif hints[param_name] is float:
                param_type = "number"
            elif hints[param_name] is bool:
                param_type = "boolean"
            elif hints[param_name] is dict:
                param_type = "object"
        properties[param_name] = {"type": param_type, "description": f"{param_name} 参数"}
        if param.default is inspect.Parameter.empty:
            required.append(param_name)
    return {"type": "object", "properties": properties, "required": required}


class SkillRegistry:
    """Skill 注册中心"""
    def __init__(self):
        self._skills: dict[str, type[FridaySkill]] = {}

    def register(self, skill_cls: type[FridaySkill]):
        if not skill_cls.name:
            raise ValueError("Skill must have a name")
        self._skills[skill_cls.name] = skill_cls
        logger.info(f"Skill registered: {skill_cls.name} v{skill_cls.version}")

    def get(self, name: str) -> type[FridaySkill] | None:
        return self._skills.get(name)

    def find_by_trigger(self, text: str) -> list[type[FridaySkill]]:
        matches = []
        for skill_cls in self._skills.values():
            if skill_cls.matches_trigger(text):
                matches.append(skill_cls)
        return sorted(matches, key=lambda s: len(s.trigger), reverse=True)

    def list_all(self) -> list[dict]:
        return [skill_cls().to_dict() for skill_cls in self._skills.values()]

    def get_tool_definitions(self, skill_name: str) -> list[dict]:
        skill_cls = self.get(skill_name)
        if skill_cls is None:
            return []
        return skill_cls.get_tool_schemas()

    def to_frontend_manifest(self) -> dict:
        """生成前端可读取的 Skill 清单"""
        from src.projects.registry import project_registry

        return {
            "skills": [
                {
                    "name": s.name,
                    "description": s.description,
                    "trigger": s.trigger,
                    "icon": s.icon,
                    "version": s.version,
                    "tools": len(s.get_tools()),
                    "project": (
                        project_registry.get_skill_manifest(s.name) or {}
                    ).get("project_id", "default"),
                    "route": (
                        project_registry.get_skill_manifest(s.name) or {}
                    ).get("route", ""),
                    "execution_mode": (
                        project_registry.get_skill_manifest(s.name) or {}
                    ).get("execution_mode", "skill_pipeline"),
                }
                for s in self._skills.values()
            ]
        }

    def apply_manifest_metadata(self):
        from src.projects.registry import project_registry

        for name, skill_cls in self._skills.items():
            manifest = project_registry.get_skill_manifest(name)
            if manifest:
                skill_cls.metadata = manifest


skill_registry = SkillRegistry()


def skill(name: str = "", trigger: str = "", description: str = "",
          version: str = "1.0.0", icon: str = "🧩"):
    """Skill 类装饰器"""
    def decorator(cls):
        cls.name = name or cls.__name__
        cls.trigger = trigger
        cls.description = description
        cls.version = version
        cls.icon = icon
        skill_registry.register(cls)
        return cls
    return decorator
