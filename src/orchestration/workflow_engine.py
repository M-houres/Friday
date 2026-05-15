"""工作流状态机 —— 多步骤、可回退、可审批、可协作

每个工作流由多个 Step 组成，Step 之间有依赖关系。
状态在服务端持久化，前端通过 SSE 订阅实时更新。
支持：回退到任意步骤、审批/拒绝、多人协作、断点续跑。
"""

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

logger = logging.getLogger(__name__)


class StepType(str, Enum):
    USER_INPUT = "user_input"       # 等用户填表单
    AGENT = "agent"                 # Agent 执行
    APPROVAL = "approval"           # 等用户审批
    OUTPUT = "output"               # 展示结果

class StepStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    AWAITING_INPUT = "awaiting_input"
    AWAITING_APPROVAL = "awaiting_approval"
    RUNNING = "running"
    COMPLETED = "completed"
    ERROR = "error"
    SKIPPED = "skipped"


@dataclass
class WorkflowStep:
    id: str
    name: str
    type: StepType
    description: str = ""
    status: StepStatus = StepStatus.PENDING
    depends_on: list[str] = field(default_factory=list)
    input_schema: dict | None = None       # JSON Schema for user input
    output: Any = None
    error: str = ""
    agent_id: str | None = None
    tool_name: str | None = None
    approval_message: str = ""
    approval_options: list[str] = field(default_factory=list)
    approval_result: str = ""               # "approved" | "rejected"
    started_at: float = 0
    completed_at: float = 0
    # UI hint
    ui_component: str = ""                  # 前端组件名
    ui_props: dict = field(default_factory=dict)


@dataclass
class WorkflowState:
    id: str
    name: str
    steps: list[WorkflowStep] = field(default_factory=list)
    current_step_index: int = 0
    state: dict = field(default_factory=dict)  # 跨步骤累积状态
    participants: list[str] = field(default_factory=list)
    created_at: float = 0
    updated_at: float = 0

    def current_step(self) -> WorkflowStep | None:
        if 0 <= self.current_step_index < len(self.steps):
            return self.steps[self.current_step_index]
        return None

    def get_step(self, step_id: str) -> WorkflowStep | None:
        for s in self.steps:
            if s.id == step_id:
                return s
        return None

    def can_go_back(self) -> bool:
        return self.current_step_index > 0

    def can_proceed(self) -> bool:
        step = self.current_step()
        if step is None:
            return False
        if step.type == StepType.APPROVAL and step.approval_result != "approved":
            return False
        if step.type == StepType.USER_INPUT and step.status != StepStatus.COMPLETED:
            return False
        return True

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "current_step_index": self.current_step_index,
            "current_step": self.current_step().id if self.current_step() else None,
            "steps": [
                {
                    "id": s.id, "name": s.name, "type": s.type.value,
                    "status": s.status.value, "description": s.description,
                    "input_schema": s.input_schema, "output": s.output,
                    "approval_message": s.approval_message,
                    "approval_options": s.approval_options,
                    "approval_result": s.approval_result,
                    "ui_component": s.ui_component, "ui_props": s.ui_props,
                    "error": s.error,
                }
                for s in self.steps
            ],
            "can_go_back": self.can_go_back(),
            "can_proceed": self.can_proceed(),
            "participants": self.participants,
        }


class WorkflowEngine:
    """工作流引擎 —— 管理多步骤工作流"""

    def __init__(self):
        self._workflows: dict[str, WorkflowState] = {}
        self._step_handlers: dict[str, Callable] = {}

    def register_step_handler(self, handler_id: str, fn: Callable):
        """注册步骤处理器"""
        self._step_handlers[handler_id] = fn

    def create_workflow(self, name: str, steps: list[dict]) -> WorkflowState:
        """创建工作流"""
        try:
            loop = asyncio.get_running_loop()
            created_at = loop.time()
        except RuntimeError:
            created_at = time.time()
        wf = WorkflowState(
            id=str(uuid.uuid4()),
            name=name,
            created_at=created_at,
        )
        for step_data in steps:
            wf.steps.append(WorkflowStep(
                id=step_data.get("id", str(uuid.uuid4())[:8]),
                name=step_data["name"],
                type=StepType(step_data.get("type", "agent")),
                description=step_data.get("description", ""),
                depends_on=step_data.get("depends_on", []),
                input_schema=step_data.get("input_schema"),
                agent_id=step_data.get("agent_id"),
                tool_name=step_data.get("tool_name"),
                ui_component=step_data.get("ui_component", ""),
                ui_props=step_data.get("ui_props", {}),
                approval_message=step_data.get("approval_message", ""),
                approval_options=step_data.get("approval_options", []),
            ))
        self._workflows[wf.id] = wf
        return wf

    def get_workflow(self, wf_id: str) -> WorkflowState | None:
        return self._workflows.get(wf_id)

    async def start_step(self, wf_id: str, step_id: str | None = None):
        """启动工作流的下一步"""
        from src.api.stream import friday_stream

        wf = self.get_workflow(wf_id)
        if wf is None:
            return

        if step_id:
            # 找到指定步骤
            for i, s in enumerate(wf.steps):
                if s.id == step_id:
                    wf.current_step_index = i
                    break

        step = wf.current_step()
        if step is None:
            return

        step.status = StepStatus.ACTIVE
        wf.updated_at = time.time()

        await friday_stream.workflow_step_start(
            step.id, step.name,
            wf.current_step_index, len(wf.steps),
            workflow_id=wf.id,
        )

        if step.type == StepType.USER_INPUT:
            step.status = StepStatus.AWAITING_INPUT
            await friday_stream.workflow_step_complete(step.id, {
                "awaiting": "user_input",
                "schema": step.input_schema,
            }, workflow_id=wf.id)

        elif step.type == StepType.APPROVAL:
            step.status = StepStatus.AWAITING_APPROVAL
            await friday_stream.workflow_approval_requested(
                step.id,
                step.approval_message or f"请审批: {step.name}",
                step.approval_options,
                workflow_id=wf.id,
            )

        elif step.type == StepType.AGENT:
            step.status = StepStatus.RUNNING
            # Agent 执行逻辑由调用方注入
            handler_key = step.tool_name or step.agent_id
            if handler_key and handler_key in self._step_handlers:
                try:
                    output = await self._step_handlers[handler_key](step, wf)
                    step.output = output
                    step.status = StepStatus.COMPLETED
                    wf.state[step.id] = output
                    await friday_stream.workflow_step_complete(step.id, output or {}, workflow_id=wf.id)
                except Exception as e:
                    step.status = StepStatus.ERROR
                    step.error = str(e)
                    await friday_stream.workflow_step_error(step.id, str(e), workflow_id=wf.id)
            else:
                step.status = StepStatus.COMPLETED
                await friday_stream.workflow_step_complete(step.id, {}, workflow_id=wf.id)

        elif step.type == StepType.OUTPUT:
            step.status = StepStatus.COMPLETED
            await friday_stream.workflow_step_complete(step.id, wf.state, workflow_id=wf.id)

    async def submit_input(self, wf_id: str, step_id: str, data: dict):
        """接收用户输入"""
        from src.api.stream import friday_stream

        wf = self.get_workflow(wf_id)
        if wf is None:
            return

        step = wf.get_step(step_id)
        if step is None:
            return

        step.output = data
        step.status = StepStatus.COMPLETED
        wf.state[step.id] = data

        await friday_stream.workflow_step_complete(step.id, data, workflow_id=wf.id)
        await self._advance(wf_id)

    async def approve(self, wf_id: str, step_id: str, approved: bool = True, comment: str = ""):
        """审批步骤"""
        wf = self.get_workflow(wf_id)
        if wf is None:
            return

        step = wf.get_step(step_id)
        if step is None:
            return

        step.approval_result = "approved" if approved else "rejected"
        step.status = StepStatus.COMPLETED
        wf.state[f"{step.id}_approval"] = {"approved": approved, "comment": comment}

        await friday_stream.workflow_step_complete(step.id, {
            "approved": approved,
            "comment": comment,
        }, workflow_id=wf.id)

        if approved:
            await self._advance(wf_id)
        else:
            # 回退到上一步
            await self.go_back(wf_id)

    async def go_back(self, wf_id: str, to_step_id: str | None = None):
        """回退"""
        from src.api.stream import friday_stream

        wf = self.get_workflow(wf_id)
        if wf is None:
            return
        if not wf.steps:
            return

        previous_index = wf.current_step_index

        if to_step_id:
            target_index = None
            for i, s in enumerate(wf.steps):
                if s.id == to_step_id:
                    target_index = i
                    break
            if target_index is None:
                return
            wf.current_step_index = target_index
        elif wf.current_step_index > 0:
            wf.current_step_index -= 1
        else:
            wf.current_step_index = 0

        await friday_stream.workflow_navigate(
            step_from=wf.steps[previous_index].id if 0 <= previous_index < len(wf.steps) else "",
            step_to=wf.steps[wf.current_step_index].id,
            workflow_id=wf.id,
        )

        await self.start_step(wf_id)

    async def _advance(self, wf_id: str):
        """前进到下一步"""
        wf = self.get_workflow(wf_id)
        if wf is None:
            return

        if wf.current_step_index + 1 < len(wf.steps):
            wf.current_step_index += 1
            await self.start_step(wf_id)
        else:
            # 工作流完成
            from src.api.stream import friday_stream
            await friday_stream.finish({"state": wf.state}, workflow_id=wf.id)


workflow_engine = WorkflowEngine()
