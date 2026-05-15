"""API 路由 —— FastAPI 接口"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import get_db
from src.config import settings
from src.models.router import model_router
from src.session.manager import SessionStore
from src.orchestration.coordinator import Coordinator
from src.tools.registry import tool_registry
from src.artifacts.service import artifact_service
from src.projects.registry import project_registry
from src.productization.async_jobs import async_job_manager
from src.api.ops_routes import router as ops_router
from src.api.schemas import (
    CreateAgentRequest, CreateSessionRequest, ForkSessionRequest,
    RollbackRequest, WorkflowRequest,
    CreateWorkflowRequest, SubmitInputRequest, ApprovalRequest,
    MemoryLearnRequest, ErrorCode, RegisterUserRequest, LoginRequest, ChangePasswordRequest,
)
from src.api.auth import get_request_user_id, issue_access_token
from src.productization.domain_services import BillingOpsService, UserOpsService

router = APIRouter(prefix="/api/v1")


# ── 健康检查 ──

@router.get("/health/live")
async def health_live():
    return {"status": "ok"}


@router.get("/health/ready")
async def health_ready():
    from src.db import check_db, check_redis
    db_ok = await check_db()
    redis_ok = await check_redis()
    all_ok = db_ok and redis_ok
    return {
        "status": "ok" if all_ok else "degraded",
        "database": db_ok,
        "redis": redis_ok,
    }


# ── Auth / Account ──

@router.post("/auth/register")
async def register_user(req: RegisterUserRequest, db: AsyncSession = Depends(get_db)):
    service = UserOpsService(db)
    try:
        account = await service.register_user(
            email=req.email,
            password=req.password,
            name=req.name,
            metadata=req.metadata,
        )
    except ValueError as exc:
        detail = str(exc)
        status_code = 409 if detail == "EMAIL_ALREADY_EXISTS" else 400
        raise HTTPException(status_code=status_code, detail=detail)
    token = issue_access_token(
        user_id=account["user_id"],
        email=account["email"],
        roles=list(account.get("roles") or []),
        name=str(account.get("name") or ""),
    )
    return {"account": account, "access_token": token, "token_type": "bearer"}


@router.post("/auth/login")
async def login_user(req: LoginRequest, db: AsyncSession = Depends(get_db)):
    service = UserOpsService(db)
    try:
        account = await service.authenticate_user(email=req.email, password=req.password)
    except ValueError as exc:
        detail = str(exc)
        status_code = 403 if detail == "USER_DISABLED" else 400
        raise HTTPException(status_code=status_code, detail=detail)
    if account is None:
        raise HTTPException(status_code=401, detail="INVALID_CREDENTIALS")
    token = issue_access_token(
        user_id=account["user_id"],
        email=account["email"],
        roles=list(account.get("roles") or []),
        name=str(account.get("name") or ""),
    )
    return {"account": account, "access_token": token, "token_type": "bearer"}


@router.get("/auth/bootstrap")
async def get_auth_bootstrap(db: AsyncSession = Depends(get_db)):
    user_count = 0
    database_available = True
    try:
        result = await db.execute(text("SELECT COUNT(*) FROM app_users"))
        user_count = int(result.scalar() or 0)
    except Exception:
        database_available = False
    return {
        "user_count": user_count,
        "registration_open": database_available,
        "first_user_becomes_admin": database_available and user_count == 0,
        "auth_mode": settings.auth_mode,
        "database_available": database_available,
    }


@router.get("/auth/me")
async def get_current_account(request: Request, db: AsyncSession = Depends(get_db)):
    account = await UserOpsService(db).get_user_account(get_request_user_id(request))
    if account is None:
        raise HTTPException(status_code=404, detail="USER_NOT_FOUND")
    return account


@router.post("/auth/change-password")
async def change_password(req: ChangePasswordRequest, request: Request, db: AsyncSession = Depends(get_db)):
    try:
        account = await UserOpsService(db).change_user_password(
            user_id=get_request_user_id(request),
            current_password=req.current_password,
            new_password=req.new_password,
        )
    except ValueError as exc:
        detail = str(exc)
        status_code = 401 if detail == "INVALID_CURRENT_PASSWORD" else 400
        raise HTTPException(status_code=status_code, detail=detail)
    if account is None:
        raise HTTPException(status_code=404, detail="USER_NOT_FOUND")
    return {"user_id": account["user_id"], "password_changed": True}


# ── Agent ──

@router.post("/agents")
async def create_agent(req: CreateAgentRequest, db: AsyncSession = Depends(get_db)):
    from sqlalchemy import text
    import json
    agent_id = str(uuid.uuid4())
    await db.execute(
        text("""
            INSERT INTO agent_definitions (id, name, system_prompt, model, strategy, tools, config, created_at, updated_at)
            VALUES (:id, :name, :prompt, :model, :strategy, :tools, :config, NOW(), NOW())
        """),
        {
            "id": agent_id, "name": req.name, "prompt": req.system_prompt,
            "model": req.model, "strategy": req.strategy,
            "tools": req.tools, "config": "{}",
        },
    )
    await db.commit()
    return {"id": agent_id, "name": req.name, "status": "idle"}


@router.get("/agents")
async def list_agents(status: str = "", limit: int = 20, db: AsyncSession = Depends(get_db)):
    from sqlalchemy import text
    query = "SELECT id, name, model, strategy, status, created_at FROM agent_definitions"
    params = {"limit": limit}
    if status:
        query += " WHERE status = :status"
        params["status"] = status
    query += " ORDER BY created_at DESC LIMIT :limit"
    rows = await db.execute(text(query), params)
    return {"agents": [dict(r._mapping) for r in rows.fetchall()]}


@router.get("/agents/{agent_id}")
async def get_agent(agent_id: str, db: AsyncSession = Depends(get_db)):
    from sqlalchemy import text
    row = await db.execute(text("SELECT * FROM agent_definitions WHERE id = :id"), {"id": agent_id})
    row = row.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Agent not found")
    return dict(row._mapping)


@router.delete("/agents/{agent_id}")
async def delete_agent(agent_id: str, db: AsyncSession = Depends(get_db)):
    from sqlalchemy import text
    await db.execute(text("DELETE FROM agent_definitions WHERE id = :id"), {"id": agent_id})
    await db.commit()
    return {"deleted": agent_id}


# ── Session ──

@router.post("/sessions")
async def create_session(req: CreateSessionRequest, db: AsyncSession = Depends(get_db)):
    store = SessionStore(db)
    result = await store.create(req.agent_id, req.user_id, req.task, req.metadata)
    return result


@router.get("/sessions/{session_id}")
async def get_session(session_id: str, db: AsyncSession = Depends(get_db)):
    store = SessionStore(db)
    session = await store.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    steps = await store.get_steps(session_id)
    session["steps"] = steps
    checkpoints = await store.get_checkpoints(session_id)
    session["checkpoints"] = checkpoints
    return session


@router.post("/sessions/{session_id}/fork")
async def fork_session(session_id: str, req: ForkSessionRequest, db: AsyncSession = Depends(get_db)):
    store = SessionStore(db)
    result = await store.fork(session_id, req.from_step)
    return result


@router.post("/sessions/{session_id}/rollback")
async def rollback_session(session_id: str, req: RollbackRequest, db: AsyncSession = Depends(get_db)):
    store = SessionStore(db)
    await store.rollback(session_id, req.to_step)
    return {"session_id": session_id, "rolled_back_to": req.to_step}


# ── 工作流 ──

@router.post("/workflows")
async def create_workflow(req: WorkflowRequest, db: AsyncSession = Depends(get_db)):
    service = BillingOpsService(db)
    charge_preview = await service.preview_user_charge(
        req.user_id,
        project_id=req.project_id or "",
        page_id=req.page_id or "",
        context=req.context,
    )
    if charge_preview["required"] and not charge_preview["can_run"]:
        raise HTTPException(
            status_code=402,
            detail=f"INSUFFICIENT_CREDITS: need {charge_preview['credits_cost']}, balance {charge_preview['credits_balance']}",
        )

    workflow_context = dict(req.context or {})
    workflow_context["_billing"] = {
        "required": charge_preview["required"],
        "credits_cost": charge_preview["credits_cost"],
        "reason": charge_preview["reason"],
        "charged": False,
    }

    if req.async_mode:
        workflow_id = str(uuid.uuid4())
        job = await async_job_manager.enqueue(
            "workflow",
            {
                "task": req.task,
                "user_id": req.user_id,
                "mode": req.mode,
                "context": workflow_context,
                "project_id": req.project_id,
                "page_id": req.page_id,
                "workflow_id": workflow_id,
            },
            priority=req.priority,
        )
        return {
            "job_id": job["job_id"],
            "workflow_id": workflow_id,
            "status": job["status"],
            "async_mode": True,
            "billing": charge_preview,
        }
    coordinator = Coordinator(db)
    result = await coordinator.execute(
        req.task,
        req.user_id,
        req.mode,
        workflow_context,
        project_id=req.project_id,
        page_id=req.page_id,
    )
    return result


@router.get("/workflows/{workflow_id}")
async def get_workflow(workflow_id: str, db: AsyncSession = Depends(get_db)):
    from sqlalchemy import text
    row = await db.execute(text("SELECT * FROM agent_workflows WHERE id = :id"), {"id": workflow_id})
    row = row.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return dict(row._mapping)


@router.get("/workflows")
async def list_workflows(status: str = "", limit: int = 20, db: AsyncSession = Depends(get_db)):
    from sqlalchemy import text
    query = "SELECT id, user_id, task, status, degradation_level, started_at, completed_at FROM agent_workflows"
    params = {"limit": limit}
    if status:
        query += " WHERE status = :status"
        params["status"] = status
    query += " ORDER BY started_at DESC LIMIT :limit"
    rows = await db.execute(text(query), params)
    return {"workflows": [dict(r._mapping) for r in rows.fetchall()]}


# ── 工具 ──

@router.get("/tools")
async def list_tools():
    return {"tools": tool_registry.list_tools()}


@router.get("/tools/{tool_name}/schema")
async def get_tool_schema(tool_name: str):
    definition = tool_registry.get_definition(tool_name)
    if not definition:
        raise HTTPException(status_code=404, detail="Tool not found")
    return definition.to_openai_schema()


# ── 统计 ──

@router.get("/stats")
async def get_stats(db: AsyncSession = Depends(get_db)):
    from sqlalchemy import text
    from src.observability.cost import cost_tracker

    workflows_result = await db.execute(text("SELECT COUNT(*) FROM agent_workflows"))
    running_result = await db.execute(text("SELECT COUNT(*) FROM agent_workflows WHERE status = 'executing'"))
    agents_result = await db.execute(text("SELECT COUNT(*) FROM agent_definitions"))
    circuits = model_router.get_circuit_states()
    cost = cost_tracker.stats()

    return {
        "workflows": {"total": workflows_result.scalar(), "running": running_result.scalar()},
        "agents": {"total": agents_result.scalar()},
        "circuit_breakers": circuits,
        "providers": model_router.list_providers(),
        "cost": {
            "total_usd": cost["total_cost_usd"],
            "today_usd": cost["total_cost_usd"],
            "total_tokens": cost["total_tokens"]["total"],
            "by_model": cost["by_model"],
        },
    }


# ── 死信队列 ──

@router.get("/dlq")
async def get_dlq(status: str = "quarantined", limit: int = 50, db: AsyncSession = Depends(get_db)):
    from sqlalchemy import text
    rows = await db.execute(
        text("SELECT * FROM task_dlq WHERE quarantine_reason = :status LIMIT :limit"),
        {"status": status, "limit": limit},
    )
    return {"items": [dict(r._mapping) for r in rows.fetchall()]}


# ── 健康检查增强 ──

@router.get("/health/deep")
async def health_deep():
    from src.observability.health import health_checker
    results = await health_checker.run_all()
    overall = await health_checker.get_overall()
    return {"status": overall.value, "components": results}


# ── 成本 ──

@router.get("/cost")
async def get_cost():
    from src.observability.cost import cost_tracker
    return cost_tracker.stats()


# ── JIT 统计 ──

@router.get("/jit/stats")
async def get_jit_stats():
    from src.orchestration.jit import jit_compiler
    return jit_compiler.get_stats()


# ── 流事件 (SSE) ──

from fastapi.responses import StreamingResponse
from src.api.stream import friday_stream, sse_generator

@router.get("/stream/{workflow_id}")
async def stream_workflow(workflow_id: str):
    """SSE 实时事件流 —— 前端 EventSource 连接"""
    return StreamingResponse(
        sse_generator(friday_stream, workflow_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── 工作流引擎 ──

from src.orchestration.workflow_engine import workflow_engine


@router.post("/engine/workflows")
async def create_engine_workflow(req: CreateWorkflowRequest):
    wf = workflow_engine.create_workflow(req.name, req.steps)
    await workflow_engine.start_step(wf.id)
    return wf.to_dict()

@router.get("/engine/workflows/{wf_id}")
async def get_engine_workflow(wf_id: str):
    wf = workflow_engine.get_workflow(wf_id)
    if wf is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return wf.to_dict()

@router.post("/engine/workflows/{wf_id}/input")
async def workflow_submit_input(wf_id: str, req: SubmitInputRequest):
    await workflow_engine.submit_input(wf_id, req.step_id, req.data)
    return {"status": "ok"}

@router.post("/engine/workflows/{wf_id}/approve")
async def workflow_approve(wf_id: str, req: ApprovalRequest):
    await workflow_engine.approve(wf_id, req.step_id, req.approved, req.comment)
    return {"status": "ok"}

@router.post("/engine/workflows/{wf_id}/back")
async def workflow_go_back(wf_id: str, to_step_id: str = ""):
    await workflow_engine.go_back(wf_id, to_step_id or None)
    return {"status": "ok"}


# ── 沙盒 ──

from src.tools.isolated_sandbox import sandbox_pool

@router.get("/sandbox/{sandbox_id}/files")
async def sandbox_list_files(sandbox_id: str, path: str = ""):
    sandbox = sandbox_pool.get(sandbox_id)
    if sandbox is None:
        raise HTTPException(status_code=404, detail="Sandbox not found")
    files = await sandbox.list_files(path)
    return {"sandbox_id": sandbox_id, "files": files}

@router.get("/sandbox/{sandbox_id}/snapshots")
async def sandbox_snapshots(sandbox_id: str):
    sandbox = sandbox_pool.get(sandbox_id)
    if sandbox is None:
        raise HTTPException(status_code=404, detail="Sandbox not found")
    return {"sandbox_id": sandbox_id, "snapshots": await sandbox.list_snapshots()}

@router.post("/sandbox/{sandbox_id}/snapshot")
async def sandbox_create_snapshot(sandbox_id: str):
    sandbox = sandbox_pool.get(sandbox_id)
    if sandbox is None:
        raise HTTPException(status_code=404, detail="Sandbox not found")
    snap_id = await sandbox.snapshot()
    return {"sandbox_id": sandbox_id, "snapshot_id": snap_id}

@router.post("/sandbox/{sandbox_id}/restore")
async def sandbox_restore(sandbox_id: str, snapshot_id: str = ""):
    sandbox = sandbox_pool.get(sandbox_id)
    if sandbox is None:
        raise HTTPException(status_code=404, detail="Sandbox not found")
    await sandbox.restore(snapshot_id)
    return {"sandbox_id": sandbox_id, "status": "restored"}


# ── Agent 工具 ──

@router.get("/agent-tools")
async def list_agent_tools():
    from src.core.agent_tools import agent_tool_registry
    return {"agents": agent_tool_registry.list_agents()}


# ── 护栏状态 ──

@router.get("/guardrails")
async def get_guardrail_stats():
    from src.core.guardrail_chain import guardrail_registry
    return guardrail_registry.stats()


# ── 持久化执行统计 ──

@router.get("/durable")
async def get_durable_stats():
    from src.orchestration.durable import durable_executor
    return {
        "mode": durable_executor.mode.value,
        "cached_tasks": len(durable_executor._task_cache),
        "checkpoints": len(durable_executor._checkpoints),
    }


# ── Skill 管理 ──

from src.tools.skill import skill_registry

@router.get("/skills")
async def list_skills():
    return skill_registry.to_frontend_manifest()

@router.get("/skills/{skill_name}")
async def get_skill_detail(skill_name: str):
    skill_cls = skill_registry.get(skill_name)
    if skill_cls is None:
        raise HTTPException(status_code=404, detail="Skill not found")
    return skill_cls().to_dict()


# ── 项目注册 ──

@router.get("/projects")
async def list_projects():
    return {"projects": [project_registry.get_project_manifest(project["id"]) for project in project_registry.list_projects()]}

@router.get("/projects/{project_id}")
async def get_project(project_id: str):
    project = project_registry.get_project_manifest(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.get("/projects/{project_id}/pages")
async def list_project_pages(project_id: str):
    project = project_registry.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"project_id": project_id, "pages": project_registry.list_project_pages(project_id)}


# ── 组件绑定 ──

from src.api.component_registry import component_registry

@router.get("/components")
async def list_components():
    return component_registry.get_all_bindings()

@router.get("/components/manifest")
async def get_component_manifest():
    return component_registry.get_manifest()


# ── 增强记忆 ──

from src.memory.enhanced_memory import enhanced_memory


@router.get("/memory/stats")
async def get_memory_stats():
    return enhanced_memory.to_dict()

@router.post("/memory/learn")
async def learn_from_task(req: MemoryLearnRequest):
    enhanced_memory.learn_from_task(req.task, req.workflow, req.result, req.success)
    return {"status": "learned"}

@router.get("/memory/context/{user_id}")
async def get_user_context(user_id: str, task: str = ""):
    return enhanced_memory.build_context(user_id, task)


# ── 主题总线 ──

from src.runtime.topic_bus import topic_bus

@router.get("/topics")
async def get_topic_stats():
    return topic_bus.get_stats()


# ── YAML 配置 ──

from src.yaml_config import yaml_config

@router.get("/config")
async def get_config_list():
    return yaml_config.list_all()


# ── Prometheus 指标 ──

from src.observability.metrics import metrics

@router.get("/metrics")
async def get_metrics():
    """Prometheus 指标端点"""
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(metrics.get_text_format(), media_type="text/plain; charset=utf-8")


# ── 错误码 ──

@router.get("/errors/codes")
async def get_error_codes():
    return {code.name: code.value for code in ErrorCode}


# ── 产物下载 ──

@router.get("/artifacts/{artifact_id}")
async def get_artifact_metadata(artifact_id: str, request: Request):
    artifact = artifact_service.require(artifact_id)
    _assert_artifact_access(request, artifact)
    return {
        "artifact_id": artifact["artifact_id"],
        "workflow_id": artifact["workflow_id"],
        "filename": artifact["filename"],
        "content_type": artifact["content_type"],
        "size_bytes": artifact["size_bytes"],
        "download_url": artifact["download_url"],
    }


@router.get("/artifacts/{artifact_id}/download")
async def download_artifact(artifact_id: str, request: Request):
    artifact = artifact_service.require(artifact_id)
    _assert_artifact_access(request, artifact)
    return FileResponse(
        artifact["path"],
        media_type=artifact["content_type"],
        filename=artifact["filename"],
    )


def _assert_artifact_access(request: Request, artifact: dict):
    if getattr(request.app.state, "auth_mode", settings.auth_mode) == "none":
        return
    requester = get_request_user_id(request)
    if requester == artifact.get("owner_user_id"):
        return
    raise HTTPException(status_code=403, detail="Forbidden")


router.include_router(ops_router)
