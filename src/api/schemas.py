"""Pydantic 请求/响应模型 —— 集中管理 API 数据结构"""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class ErrorCode(str, Enum):
    """统一错误码"""
    # 通用
    INTERNAL_ERROR = "INTERNAL_ERROR"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    NOT_FOUND = "NOT_FOUND"
    UNAUTHORIZED = "UNAUTHORIZED"
    RATE_LIMITED = "RATE_LIMITED"

    # 工作流
    WORKFLOW_NOT_FOUND = "WORKFLOW_NOT_FOUND"
    WORKFLOW_TIMEOUT = "WORKFLOW_TIMEOUT"
    WORKFLOW_STALLED = "WORKFLOW_STALLED"
    WORKFLOW_DEGRADED = "WORKFLOW_DEGRADED"

    # 模型
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    MODEL_RATE_LIMITED = "MODEL_RATE_LIMITED"
    MODEL_CIRCUIT_OPEN = "MODEL_CIRCUIT_OPEN"
    MODEL_TIMEOUT = "MODEL_TIMEOUT"

    # 工具
    TOOL_NOT_FOUND = "TOOL_NOT_FOUND"
    TOOL_EXECUTION_FAILED = "TOOL_EXECUTION_FAILED"
    TOOL_TIMEOUT = "TOOL_TIMEOUT"

    # Agent
    AGENT_NOT_FOUND = "AGENT_NOT_FOUND"
    AGENT_STEP_LIMIT = "AGENT_STEP_LIMIT"

    # 数据库
    DB_CONNECTION_FAILED = "DB_CONNECTION_FAILED"
    REDIS_CONNECTION_FAILED = "REDIS_CONNECTION_FAILED"

    # 编排
    PLAN_FAILED = "PLAN_FAILED"
    DISPATCH_FAILED = "DISPATCH_FAILED"
    AGGREGATE_FAILED = "AGGREGATE_FAILED"


class APIError(BaseModel):
    """统一错误响应"""
    error: ErrorCode
    message: str
    detail: str | None = None
    trace_id: str | None = None


class CreateAgentRequest(BaseModel):
    name: str
    system_prompt: str
    model: str = "deepseek-chat"
    tools: list[str] = []
    strategy: str = "react"


class CreateSessionRequest(BaseModel):
    agent_id: str
    user_id: str = "default"
    task: str
    metadata: dict | None = None


class ForkSessionRequest(BaseModel):
    from_step: int = 0


class RollbackRequest(BaseModel):
    to_step: int = 0


class WorkflowRequest(BaseModel):
    user_id: str = "default"
    task: str
    mode: str = "auto"
    context: dict | None = None
    project_id: str | None = None
    page_id: str | None = None
    async_mode: bool = False
    priority: int = 5


class RegisterUserRequest(BaseModel):
    email: str
    password: str = Field(min_length=8)
    name: str = ""
    metadata: dict | None = None


class LoginRequest(BaseModel):
    email: str
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)


class RegisterToolRequest(BaseModel):
    name: str
    description: str
    parameters: dict
    handler: str


class CreateWorkflowRequest(BaseModel):
    name: str
    steps: list[dict]


class SubmitInputRequest(BaseModel):
    step_id: str
    data: dict


class ApprovalRequest(BaseModel):
    step_id: str
    approved: bool = True
    comment: str = ""


class ReviewApprovalRequest(BaseModel):
    approved: bool = True
    comment: str = ""


class MemoryLearnRequest(BaseModel):
    task: str
    workflow: str
    result: dict
    success: bool = True


class HealthResponse(BaseModel):
    status: str


class HealthReadyResponse(BaseModel):
    status: str
    database: bool
    redis: bool


class WorkflowResponse(BaseModel):
    workflow_id: str
    status: str
    result: dict | None = None
    dags: dict | None = None
    degradation_level: int = 0
    failed_nodes: list[str] = []


class StatsResponse(BaseModel):
    workflows: dict
    agents: dict
    circuit_breakers: list[dict]
    providers: list[str]
    cost: dict


class SkillManifest(BaseModel):
    skills: list[dict]


class ComponentManifest(BaseModel):
    version: str
    components: dict
    defaultComponents: dict
