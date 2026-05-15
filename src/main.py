"""入口 —— FastAPI 应用启动 v1.0"""

import os
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.db import init_db, close_db, engine
from src.observability.logger import setup_logging
from src.observability.health import health_checker, self_healer
from src.orchestration.dlq_worker import DLQWorker
from src.orchestration.durable import durable_executor
from src.orchestration.coordinator import Coordinator
from src.api.routes import router
from src.api.panel import get_panel_html
from src.api.auth import AuthMiddleware, resolve_jwt_secret
from src.api.ratelimit import RateLimitMiddleware
from src.productization.async_jobs import AsyncJobStore, async_job_manager
from src.db import async_session

_dlq_worker: DLQWorker = None

setup_logging()
logger = logging.getLogger(__name__)


def _validate_runtime_settings():
    if settings.environment != "prod":
        return
    if settings.auth_mode == "none":
        raise RuntimeError("Production mode requires AUTH_MODE=api_key or AUTH_MODE=jwt")
    if settings.auth_mode == "api_key" and not settings.api_keys.strip():
        raise RuntimeError("Production mode with api_key auth requires API_KEYS")
    if settings.auth_mode == "jwt" and not settings.jwt_secret.strip():
        raise RuntimeError("Production mode with jwt auth requires JWT_SECRET")


_validate_runtime_settings()


def _role_enabled(*roles: str) -> bool:
    return settings.runtime_role in roles or settings.runtime_role == "all"


def _is_test_runtime() -> bool:
    return bool(os.getenv("PYTEST_CURRENT_TEST"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Friday v1.0...")
    db_available = True
    try:
        await init_db()
    except Exception as exc:
        db_available = False
        logger.warning("Database unavailable during startup, continuing in degraded mode: %s", exc)
    app.state.database_available = db_available

    durable_session = AsyncSession(engine) if db_available else None
    dlq_session = AsyncSession(engine) if db_available else None
    durable_executor.set_db(durable_session)

    start_background_workers = not _is_test_runtime()

    # 启动自愈器
    if start_background_workers and _role_enabled("api"):
        await self_healer.start()

    # 启动死信队列 Worker
    global _dlq_worker
    if start_background_workers and _role_enabled("worker"):
        _dlq_worker = DLQWorker(dlq_session)
        await _dlq_worker.start()

    async def execute_async_job(job_id: str, payload: dict) -> dict:
        if not db_available:
            raise RuntimeError("Database unavailable; async workflow execution is disabled")
        worker_session = AsyncSession(engine)
        try:
            coordinator = Coordinator(worker_session)
            return await coordinator.execute(
                payload.get("task", ""),
                payload.get("user_id", "default"),
                payload.get("mode", "auto"),
                payload.get("context"),
                project_id=payload.get("project_id"),
                page_id=payload.get("page_id"),
                workflow_id=payload.get("workflow_id"),
            )
        finally:
            await worker_session.close()

    async_job_manager.configure(
        execute_async_job,
        store=AsyncJobStore(async_session) if db_available else None,
        worker_name=settings.async_worker_name,
        worker_mode="database" if db_available and settings.runtime_role == "worker" else "memory",
    )
    if start_background_workers and _role_enabled("worker"):
        await async_job_manager.start()

    logger.info(f"Friday v1.0 ready | {settings.default_model} | {len(durable_executor._task_cache)} cached tasks")
    yield

    # 优雅关闭：中断进行中的工作流
    logger.info("Shutting down gracefully...")
    try:
        if durable_session is not None:
            from sqlalchemy import text
            await durable_session.execute(
                text("""
                    UPDATE agent_workflows
                    SET status = 'failed',
                        error = 'Server shutdown — workflow interrupted',
                        completed_at = NOW()
                    WHERE status IN ('planning', 'dispatching', 'executing', 'aggregating')
                """),
            )
            await durable_session.commit()
            logger.info("In-flight workflows marked as interrupted")
    except Exception as e:
        logger.warning(f"Failed to interrupt workflows: {e}")

    if _dlq_worker:
        await _dlq_worker.stop()
    if start_background_workers and _role_enabled("worker"):
        await async_job_manager.stop()
    if start_background_workers and _role_enabled("api"):
        await self_healer.stop()
    if durable_session is not None:
        await durable_session.close()
    if dlq_session is not None:
        await dlq_session.close()
    await close_db()
    logger.info("Friday stopped")


app = FastAPI(
    title="星期五 (Friday)",
    description="通用 AI Agent 应用引擎 —— 多步骤工作流 + 持久化执行 + SSE 实时流",
    version="1.0.0",
    lifespan=lifespan,
)
app.state.auth_mode = settings.auth_mode

_cors_origins = [o.strip() for o in settings.cors_allow_origins.split(",") if o.strip()]
if not _cors_origins:
    _cors_origins = ["http://localhost:3000"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_static_dir = Path(__file__).resolve().parents[2] / "static"
_static_dir.mkdir(parents=True, exist_ok=True)
if not any(getattr(route, "path", "") == "/static" for route in app.router.routes):
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")

# 认证中间件 (默认关闭, 设置 AUTH_MODE=api_key 或 jwt 开启)
if settings.auth_mode == "api_key" and settings.api_keys:
    app.add_middleware(AuthMiddleware, auth_mode="api_key",
                       api_keys={k.strip() for k in settings.api_keys.split(",") if k.strip()})
elif settings.auth_mode == "jwt" and resolve_jwt_secret():
    app.add_middleware(AuthMiddleware, auth_mode="jwt",
                       jwt_secret=resolve_jwt_secret(),
                       jwt_algorithm=settings.jwt_algorithm)

# 速率限制中间件
app.add_middleware(RateLimitMiddleware,
                   global_rpm=settings.rate_limit_global_rpm,
                   user_rpm=settings.rate_limit_user_rpm,
                   ip_rpm=settings.rate_limit_ip_rpm)

app.include_router(router)


# ── 全局错误处理 ──

from fastapi import Request
from fastapi.responses import JSONResponse
from src.api.schemas import APIError, ErrorCode


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled error: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content=APIError(
            error=ErrorCode.INTERNAL_ERROR,
            message=str(exc),
            detail=type(exc).__name__,
        ).model_dump(),
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc):
    return JSONResponse(
        status_code=exc.status_code,
        content=APIError(
            error=ErrorCode.VALIDATION_ERROR if exc.status_code == 422 else ErrorCode.INTERNAL_ERROR,
            message=exc.detail,
        ).model_dump(),
    )


@app.get("/")
async def root():
    return {
        "name": "星期五 (Friday)",
        "version": "1.0.0",
        "docs": "/docs",
        "panel": "/panel",
        "stream": "/api/v1/stream/{workflow_id}",
        "health": "/api/v1/health/live",
    }


@app.get("/panel", response_class=HTMLResponse)
async def panel():
    return get_panel_html()


def main():
    import uvicorn
    uvicorn.run(
        "src.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        reload=False,
    )


if __name__ == "__main__":
    main()
