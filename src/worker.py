"""Standalone async worker for Friday jobs."""

from __future__ import annotations

import asyncio
import logging
import signal

from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.db import async_session, close_db, engine, init_db
from src.observability.logger import setup_logging
from src.orchestration.coordinator import Coordinator
from src.orchestration.dlq_worker import DLQWorker
from src.productization.async_jobs import AsyncJobStore, async_job_manager

setup_logging()
logger = logging.getLogger(__name__)


async def run_worker():
    await init_db()
    stop_event = asyncio.Event()
    worker_session = AsyncSession(engine)
    dlq_worker = DLQWorker(worker_session)

    async def execute_async_job(job_id: str, payload: dict) -> dict:
        session = AsyncSession(engine)
        try:
            coordinator = Coordinator(session)
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
            await session.close()

    async_job_manager.configure(
        execute_async_job,
        store=AsyncJobStore(async_session),
        worker_name=settings.async_worker_name,
        worker_mode="database",
    )
    await dlq_worker.start()
    await async_job_manager.start()
    logger.info("Friday worker started: %s", settings.async_worker_name)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    await stop_event.wait()

    await async_job_manager.stop()
    await dlq_worker.stop()
    await worker_session.close()
    await close_db()
    logger.info("Friday worker stopped")


def main():
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
