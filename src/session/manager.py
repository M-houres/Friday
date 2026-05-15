"""Session 管理 —— 创建、归档、Fork、Rollback、Checkpoint"""

import json
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import get_db, get_redis
from src.config import settings

logger = logging.getLogger(__name__)


class SessionStore:
    """Session 持久化管理"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(
        self, agent_id: str, user_id: str, task: str, metadata: dict | None = None
    ) -> dict:
        session_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        meta_json = json.dumps(metadata or {}, ensure_ascii=False)

        await self.db.execute(
            text("""
                INSERT INTO sessions (id, agent_id, user_id, task, status, metadata, created_at, updated_at)
                VALUES (:id, :agent_id, :user_id, :task, 'created', :metadata, :now, :now)
            """),
            {"id": session_id, "agent_id": agent_id, "user_id": user_id, "task": task, "metadata": meta_json, "now": now},
        )
        await self.db.commit()

        logger.info(f"Session created: {session_id}")
        return {"session_id": session_id, "status": "created"}

    async def get(self, session_id: str) -> dict | None:
        row = await self.db.execute(
            text("SELECT * FROM sessions WHERE id = :id"),
            {"id": session_id},
        )
        row = row.fetchone()
        return dict(row._mapping) if row else None

    async def update_status(self, session_id: str, status: str, error: str = ""):
        await self.db.execute(
            text("UPDATE sessions SET status = :status, error = :error, updated_at = NOW() WHERE id = :id"),
            {"id": session_id, "status": status, "error": error},
        )
        await self.db.commit()

    async def add_step(
        self, session_id: str, step_index: int, step_type: str, content: dict,
        model: str = "", tokens_used: int = 0, latency_ms: int = 0, error: str = "",
    ) -> str:
        step_id = str(uuid.uuid4())
        await self.db.execute(
            text("""
                INSERT INTO session_steps (id, session_id, step_index, type, content, model, tokens_used, latency_ms, error)
                VALUES (:id, :session_id, :step_index, :type, :content, :model, :tokens_used, :latency_ms, :error)
            """),
            {
                "id": step_id, "session_id": session_id, "step_index": step_index,
                "type": step_type, "content": json.dumps(content, ensure_ascii=False),
                "model": model, "tokens_used": tokens_used, "latency_ms": latency_ms, "error": error,
            },
        )
        await self.db.execute(
            text("UPDATE sessions SET current_step = :step, updated_at = NOW() WHERE id = :id"),
            {"id": session_id, "step": step_index},
        )
        await self.db.commit()
        return step_id

    async def get_steps(self, session_id: str, limit: int = 50) -> list[dict]:
        rows = await self.db.execute(
            text("SELECT * FROM session_steps WHERE session_id = :id ORDER BY step_index ASC LIMIT :limit"),
            {"id": session_id, "limit": limit},
        )
        return [dict(r._mapping) for r in rows.fetchall()]

    async def create_checkpoint(self, session_id: str, step_index: int, state: dict) -> str:
        cp_id = str(uuid.uuid4())
        await self.db.execute(
            text("""
                INSERT INTO session_checkpoints (id, session_id, step_index, state)
                VALUES (:id, :session_id, :step_index, :state)
            """),
            {"id": cp_id, "session_id": session_id, "step_index": step_index, "state": json.dumps(state, ensure_ascii=False)},
        )
        await self.db.commit()
        return cp_id

    async def get_latest_checkpoint(self, session_id: str) -> dict | None:
        row = await self.db.execute(
            text("SELECT * FROM session_checkpoints WHERE session_id = :id ORDER BY step_index DESC LIMIT 1"),
            {"id": session_id},
        )
        row = row.fetchone()
        return dict(row._mapping) if row else None

    async def get_checkpoints(self, session_id: str) -> list[dict]:
        rows = await self.db.execute(
            text("SELECT id, step_index, created_at FROM session_checkpoints WHERE session_id = :id ORDER BY step_index ASC"),
            {"id": session_id},
        )
        return [dict(r._mapping) for r in rows.fetchall()]

    async def fork(self, session_id: str, from_step: int) -> dict:
        """从指定步骤分叉，创建新 session"""
        new_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)

        # 复制原 session 元数据
        original = await self.get(session_id)
        if not original:
            raise ValueError(f"Session not found: {session_id}")

        await self.db.execute(
            text("""
                INSERT INTO sessions (id, agent_id, user_id, task, status, current_step, metadata, created_at, updated_at)
                VALUES (:id, :agent_id, :user_id, :task, 'created', :step, :metadata, :now, :now)
            """),
            {
                "id": new_id, "agent_id": original["agent_id"], "user_id": original["user_id"],
                "task": f"[Fork from {session_id}] {original['task']}",
                "step": from_step,
                "metadata": json.dumps({"forked_from": session_id, "forked_at_step": from_step}, ensure_ascii=False),
                "now": now,
            },
        )

        # 复制步骤
        steps = await self.get_steps(session_id)
        for step in steps:
            if step["step_index"] <= from_step:
                await self.add_step(
                    new_id, step["step_index"], step["type"],
                    step["content"], step.get("model", ""),
                    step.get("tokens_used", 0), step.get("latency_ms", 0),
                )

        await self.db.commit()
        return {"session_id": new_id, "forked_from": session_id, "forked_at_step": from_step}

    async def rollback(self, session_id: str, to_step: int):
        """回滚到指定步骤"""
        # 删除 > to_step 的步骤
        await self.db.execute(
            text("DELETE FROM session_steps WHERE session_id = :id AND step_index > :step"),
            {"id": session_id, "step": to_step},
        )
        # 更新 current_step
        await self.db.execute(
            text("UPDATE sessions SET current_step = :step, updated_at = NOW() WHERE id = :id"),
            {"id": session_id, "step": to_step},
        )
        await self.db.commit()

    async def complete(self, session_id: str, result: dict, degradation_level: int = 0):
        await self.db.execute(
            text("""
                UPDATE sessions
                SET status = 'completed', result = :result, degradation_level = :level, updated_at = NOW()
                WHERE id = :id
            """),
            {"id": session_id, "result": json.dumps(result, ensure_ascii=False), "level": degradation_level},
        )
        await self.db.commit()

    async def fail(self, session_id: str, error: str):
        await self.db.execute(
            text("UPDATE sessions SET status = 'failed', error = :error, updated_at = NOW() WHERE id = :id"),
            {"id": session_id, "error": error},
        )
        await self.db.commit()
