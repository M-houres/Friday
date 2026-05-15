"""Simple async job queue with optional database persistence."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.config import settings
from src.observability.metrics import metrics
from src.productization.domain_services import WorkflowOpsService

logger = logging.getLogger(__name__)


class AsyncJobStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._session_factory = session_factory

    async def create(self, job: dict) -> dict:
        async with self._session_factory() as session:
            return await WorkflowOpsService(session).create_async_job(
                job["job_id"],
                job["job_type"],
                job["payload"],
                priority=job["priority"],
                status=job["status"],
                result=job["result"],
                error=job["error"],
                created_at=job["created_at"],
                started_at=job["started_at"],
                completed_at=job["completed_at"],
            )

    async def update(self, job_id: str, **changes) -> dict | None:
        async with self._session_factory() as session:
            return await WorkflowOpsService(session).update_async_job(job_id, **changes)

    async def recover(self) -> list[dict]:
        async with self._session_factory() as session:
            return await WorkflowOpsService(session).recover_async_jobs()

    async def claim_next(self, worker_name: str) -> dict | None:
        async with self._session_factory() as session:
            return await WorkflowOpsService(session).claim_next_async_job(worker_name)

    async def get(self, job_id: str) -> dict | None:
        async with self._session_factory() as session:
            return await WorkflowOpsService(session).get_async_job(job_id)

    async def heartbeat(self, job_id: str, worker_name: str) -> dict | None:
        async with self._session_factory() as session:
            return await WorkflowOpsService(session).heartbeat_async_job(job_id, worker_name)

    async def recycle_stale_async_jobs(self, stale_after_s: int) -> int:
        async with self._session_factory() as session:
            return await WorkflowOpsService(session).recycle_stale_async_jobs(stale_after_s)


class AsyncJobManager:
    def __init__(self):
        self._queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self._jobs: dict[str, dict] = {}
        self._executor: Callable[[str, dict], Awaitable[dict]] | None = None
        self._store: AsyncJobStore | None = None
        self._worker_task: asyncio.Task | None = None
        self._running = False
        self._worker_name = settings.async_worker_name
        self._worker_mode = "memory"
        self._heartbeat_tasks: dict[str, asyncio.Task] = {}

    def configure(
        self,
        executor: Callable[[str, dict], Awaitable[dict]],
        *,
        store: AsyncJobStore | None = None,
        worker_name: str | None = None,
        worker_mode: str = "memory",
    ):
        self._executor = executor
        self._store = store
        self._worker_name = worker_name or settings.async_worker_name
        self._worker_mode = worker_mode

    async def start(self):
        if self._running:
            return
        self._queue = asyncio.PriorityQueue()
        self._running = True
        if self._worker_mode == "memory":
            await self.recover_pending_jobs()
        self._worker_task = asyncio.create_task(self._worker_loop())

    async def stop(self):
        self._running = False
        for task in list(self._heartbeat_tasks.values()):
            task.cancel()
        self._heartbeat_tasks.clear()
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except (asyncio.CancelledError, RuntimeError):
                pass
            self._worker_task = None
        self._queue = asyncio.PriorityQueue()

    async def recover_pending_jobs(self):
        if self._store is None:
            return
        jobs = await self._store.recover()
        for job in jobs:
            normalized = self._normalize_persisted_job(job)
            self._jobs[normalized["job_id"]] = normalized
            await self._queue.put((normalized["priority"], normalized["created_at"], normalized["job_id"]))

    async def enqueue(self, job_type: str, payload: dict, priority: int = 5) -> dict:
        job_id = uuid.uuid4().hex
        job = {
            "job_id": job_id,
            "job_type": job_type,
            "status": "queued",
            "priority": priority,
            "payload": payload,
            "result": None,
            "error": "",
            "created_at": time.time(),
            "started_at": None,
            "completed_at": None,
        }
        self._jobs[job_id] = job
        if self._store is not None:
            await self._store.create(job)
        if self._worker_mode == "memory":
            await self._queue.put((priority, job["created_at"], job_id))
        return dict(job)

    def get(self, job_id: str) -> dict | None:
        job = self._jobs.get(job_id)
        return dict(job) if job else None

    def list_jobs(self, status: str = "", limit: int = 100) -> list[dict]:
        jobs = list(self._jobs.values())
        if status:
            jobs = [job for job in jobs if job["status"] == status]
        jobs.sort(key=lambda item: item["created_at"], reverse=True)
        return [dict(job) for job in jobs[:limit]]

    async def cancel(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if job is not None and job["status"] == "queued":
            job["status"] = "cancelled"
            job["completed_at"] = time.time()
            if self._store is not None:
                await self._store.update(
                    job_id,
                    status="cancelled",
                    completed_at=job["completed_at"],
                    error="Cancelled by user",
                )
            return True

        if self._store is not None:
            current = await self._store.get(job_id)
            if not current or current.get("status") != "queued":
                return False
            updated = await self._store.update(job_id, status="cancelled", completed_at=time.time(), error="Cancelled by user")
            if updated and updated.get("status") == "cancelled":
                self._jobs[job_id] = self._normalize_persisted_job(updated)
                return True
        return False

    async def _worker_loop(self):
        while self._running:
            if self._worker_mode == "database":
                await self._work_once_from_database()
                await asyncio.sleep(settings.async_jobs_poll_interval_s)
                continue

            priority, _, job_id = await self._queue.get()
            job = self._jobs.get(job_id)
            if not job or job["status"] != "queued":
                continue
            await self._execute_job(job)

    async def _work_once_from_database(self):
        if self._store is None:
            return
        recycled = await self._store.recycle_stale_async_jobs(settings.async_jobs_stale_after_s)
        if recycled:
            metrics.counter_inc("friday_async_jobs_recycled_total", recycled, {"worker": self._worker_name})
        claimed = await self._store.claim_next(self._worker_name)
        if claimed is None:
            return
        metrics.counter_inc("friday_async_jobs_claimed_total", 1, {"worker": self._worker_name})
        job = self._normalize_persisted_job(claimed)
        self._jobs[job["job_id"]] = job
        await self._execute_job(job, persist_running=False)

    async def _execute_job(self, job: dict, *, persist_running: bool = True):
        if self._executor is None:
            job["status"] = "failed"
            job["error"] = "No async job executor configured"
            job["completed_at"] = time.time()
            await self._persist_job(job)
            return

        job["status"] = "running"
        job["started_at"] = job.get("started_at") or time.time()
        if persist_running:
            await self._persist_job(job)
        heartbeat_task = self._start_heartbeat(job["job_id"])
        try:
            result = await self._executor(job["job_id"], dict(job["payload"]))
            job["status"] = "completed"
            job["result"] = result
            job["error"] = ""
            metrics.counter_inc("friday_async_jobs_completed_total", 1, {"worker": self._worker_name})
        except Exception as exc:
            logger.error("Async job failed %s: %s", job["job_id"], exc)
            job["status"] = "failed"
            job["error"] = str(exc)
            metrics.counter_inc("friday_async_jobs_failed_total", 1, {"worker": self._worker_name})
        finally:
            if heartbeat_task:
                heartbeat_task.cancel()
                self._heartbeat_tasks.pop(job["job_id"], None)
            metrics.gauge_set("friday_async_jobs_heartbeats_active", float(len(self._heartbeat_tasks)), {"worker": self._worker_name})
            job["completed_at"] = time.time()
            await self._persist_job(job)

    async def _persist_job(self, job: dict):
        if self._store is None:
            return
        updated = await self._store.update(
            job["job_id"],
            status=job["status"],
            priority=job["priority"],
            payload=job["payload"],
            result=job["result"],
            error=job["error"],
            worker_name=self._worker_name if job["status"] == "running" else "",
            started_at=job["started_at"],
            heartbeat_at=time.time() if job["status"] == "running" else job.get("completed_at"),
            completed_at=job["completed_at"],
        )
        if updated:
            self._jobs[job["job_id"]] = self._normalize_persisted_job(updated)

    def _start_heartbeat(self, job_id: str) -> asyncio.Task | None:
        if self._store is None:
            return None

        async def beat():
            while self._running:
                await asyncio.sleep(settings.async_jobs_heartbeat_interval_s)
                try:
                    await self._store.heartbeat(job_id, self._worker_name)
                except Exception as exc:
                    logger.debug("Async job heartbeat failed %s: %s", job_id, exc)

        task = asyncio.create_task(beat())
        self._heartbeat_tasks[job_id] = task
        metrics.gauge_set("friday_async_jobs_heartbeats_active", float(len(self._heartbeat_tasks)), {"worker": self._worker_name})
        return task

    @staticmethod
    def _normalize_persisted_job(job: dict) -> dict:
        created_at = job.get("created_at")
        started_at = job.get("started_at")
        completed_at = job.get("completed_at")
        return {
            "job_id": str(job.get("job_id") or job.get("id") or ""),
            "job_type": job.get("job_type", ""),
            "status": job.get("status", "queued"),
            "priority": int(job.get("priority") or 5),
            "payload": job.get("payload") or {},
            "result": job.get("result"),
            "error": job.get("error") or "",
            "created_at": created_at.timestamp() if hasattr(created_at, "timestamp") else float(created_at or time.time()),
            "started_at": started_at.timestamp() if hasattr(started_at, "timestamp") else started_at,
            "completed_at": completed_at.timestamp() if hasattr(completed_at, "timestamp") else completed_at,
        }


async_job_manager = AsyncJobManager()
