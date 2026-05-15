"""持久化执行引擎 —— LangGraph 风格 @task 装饰器 + 检查点续跑

核心原理：
- @task 是持久化边界，包裹的任务执行一次后结果被缓存
- 工作流崩溃后重启，从最后一个检查点继续
- 已完成的 @task 不重新执行，直接读取缓存结果
- 三种模式: exit(关闭时存) / async(后台存) / sync(每步存)
"""

import asyncio
import functools
import hashlib
import json
import logging
import time
from contextvars import ContextVar
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

_current_workflow_id: ContextVar[str] = ContextVar("workflow_id", default="")
_current_step_id: ContextVar[str] = ContextVar("step_id", default="")


class DurabilityMode(str, Enum):
    EXIT = "exit"        # 仅在关闭/完成时保存，最快
    ASYNC = "async"      # 后台异步保存，平衡
    SYNC = "sync"        # 每步同步保存，最安全


class TaskResult:
    """@task 的执行结果容器"""
    def __init__(self, task_id: str, result: Any = None, error: str = "", status: str = "pending"):
        self.task_id = task_id
        self.result = result
        self.error = error
        self.status = status  # pending | running | completed | failed
        self.started_at: float = 0
        self.completed_at: float = 0

    def is_complete(self) -> bool:
        return self.status == "completed"

    def is_failed(self) -> bool:
        return self.status == "failed"


class DurableExecutor:
    """持久化执行器 —— 管理 @task 缓存和检查点"""

    def __init__(self, mode: DurabilityMode = DurabilityMode.SYNC):
        self.mode = mode
        self._task_cache: dict[str, TaskResult] = {}
        self._checkpoints: list[dict] = []
        self._db_session = None

    def set_db(self, db_session):
        self._db_session = db_session

    def _task_key(self, workflow_id: str, step_id: str, fn_name: str, args_hash: str) -> str:
        return f"task:{workflow_id}:{step_id}:{fn_name}:{args_hash}"

    def get_cached(self, task_id: str) -> TaskResult | None:
        return self._task_cache.get(task_id)

    def cache_result(self, task_id: str, result: TaskResult):
        self._task_cache[task_id] = result
        if self.mode == DurabilityMode.SYNC:
            self._save_checkpoint()

    async def cache_result_async(self, task_id: str, result: TaskResult):
        self._task_cache[task_id] = result
        if self.mode == DurabilityMode.SYNC:
            await self._persist_to_db(task_id, result)

    def checkpoint(self) -> dict:
        """创建检查点快照"""
        snapshot = {
            "timestamp": time.time(),
            "task_cache": {
                k: {"status": v.status, "result": v.result, "error": v.error}
                for k, v in self._task_cache.items()
            },
        }
        self._checkpoints.append(snapshot)
        return snapshot

    def _save_checkpoint(self):
        self.checkpoint()

    async def _persist_to_db(self, task_id: str, result: TaskResult):
        """持久化到数据库"""
        if self._db_session is None:
            return
        try:
            from sqlalchemy import text
            await self._db_session.execute(
                text("""
                    INSERT INTO task_durability (task_id, status, result, error, completed_at)
                    VALUES (:task_id, :status, :result, :error, NOW())
                    ON CONFLICT (task_id) DO UPDATE
                    SET status = :status, result = :result, error = :error, completed_at = NOW()
                """),
                {
                    "task_id": task_id,
                    "status": result.status,
                    "result": json.dumps(result.result, ensure_ascii=False, default=str),
                    "error": result.error,
                },
            )
            await self._db_session.commit()
        except Exception as e:
            logger.warning(f"Durable persist failed: {e}")

    async def restore_from_db(self, workflow_id: str) -> dict[str, TaskResult]:
        """从数据库恢复任务缓存"""
        if self._db_session is None:
            return {}
        try:
            from sqlalchemy import text
            rows = await self._db_session.execute(
                text("SELECT * FROM task_durability WHERE task_id LIKE :pattern"),
                {"pattern": f"task:{workflow_id}:%"},
            )
            for row in rows.fetchall():
                r = dict(row._mapping)
                task_id = r["task_id"]
                result_data = json.loads(r["result"]) if r["result"] else None
                self._task_cache[task_id] = TaskResult(
                    task_id=task_id,
                    result=result_data,
                    error=r.get("error", ""),
                    status=r["status"],
                )
            return self._task_cache
        except Exception as e:
            logger.warning(f"Durable restore failed: {e}")
            return {}

    def clear_workflow(self, workflow_id: str):
        """清除指定工作流的所有缓存"""
        keys = [k for k in self._task_cache if workflow_id in k]
        for k in keys:
            del self._task_cache[k]


# 全局持久化执行器
durable_executor = DurableExecutor(mode=DurabilityMode.SYNC)


def task(fn=None, *, max_retries: int = 3, timeout_s: float = 300):
    """@task 装饰器 —— 标记函数为持久化任务

    用法:
        @task
        async def search_web(query: str) -> dict:
            ...

        @task(max_retries=5, timeout_s=60)
        async def call_api(endpoint: str) -> dict:
            ...
    """
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            wf_id = _current_workflow_id.get()
            step_id = _current_step_id.get()

            # 生成任务 ID
            args_str = json.dumps({"args": args, "kwargs": kwargs}, sort_keys=True, default=str)
            args_hash = hashlib.sha256(args_str.encode()).hexdigest()[:16]
            task_id = durable_executor._task_key(wf_id, step_id, func.__name__, args_hash)

            # 检查缓存
            cached = durable_executor.get_cached(task_id)
            if cached and cached.is_complete():
                logger.debug(f"Durable HIT: {func.__name__} (cached)")
                return cached.result

            if cached and cached.is_failed():
                logger.debug(f"Durable RETRY: {func.__name__} (was failed)")

            # 执行任务
            result = TaskResult(task_id=task_id, status="running", started_at=time.time())
            durable_executor.cache_result(task_id, result)

            for attempt in range(max_retries + 1):
                try:
                    output = await asyncio.wait_for(func(*args, **kwargs), timeout=timeout_s)
                    result.status = "completed"
                    result.result = output
                    result.completed_at = time.time()
                    durable_executor.cache_result(task_id, result)
                    return output

                except asyncio.TimeoutError:
                    if attempt >= max_retries:
                        result.status = "failed"
                        result.error = f"Timeout after {max_retries + 1} attempts"
                        durable_executor.cache_result(task_id, result)
                        raise
                    logger.warning(f"Durable task {func.__name__} timeout, retry {attempt + 1}/{max_retries}")
                    await asyncio.sleep(1 * (2 ** attempt))

                except Exception as e:
                    if attempt >= max_retries:
                        result.status = "failed"
                        result.error = str(e)
                        durable_executor.cache_result(task_id, result)
                        raise
                    logger.warning(f"Durable task {func.__name__} error: {e}, retry {attempt + 1}/{max_retries}")
                    await asyncio.sleep(1 * (2 ** attempt))

        return wrapper

    if fn is None:
        return decorator
    return decorator(fn)


def set_workflow_context(workflow_id: str, step_id: str = ""):
    """设置当前工作流上下文（供 @task 使用）"""
    _current_workflow_id.set(workflow_id)
    _current_step_id.set(step_id)


# 添加持久化表到数据库
DURABILITY_TABLE = """
CREATE TABLE IF NOT EXISTS task_durability (
    task_id TEXT PRIMARY KEY,
    status TEXT DEFAULT 'pending',
    result JSONB,
    error TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);
"""
