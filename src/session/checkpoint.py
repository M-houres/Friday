"""Session 检查点 —— Fork / Rollback / Replay / Snapshot"""

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class CheckpointManager:
    """会话检查点管理器 —— 类 Git 操作：保存、分叉、回滚、重放"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def save(self, session_id: str, step_index: int, state: dict) -> str:
        """保存一个检查点"""
        checkpoint_id = str(uuid.uuid4())
        await self.db.execute(
            text("""
                INSERT INTO session_checkpoints (id, session_id, step_index, state, created_at)
                VALUES (:id, :session_id, :step_index, :state, NOW())
            """),
            {
                "id": checkpoint_id,
                "session_id": session_id,
                "step_index": step_index,
                "state": json.dumps(state, ensure_ascii=False, default=str),
            },
        )
        await self.db.commit()
        logger.debug(f"Checkpoint saved: {checkpoint_id} at step {step_index}")
        return checkpoint_id

    async def get_latest(self, session_id: str) -> Optional[dict]:
        """获取最新检查点"""
        row = await self.db.execute(
            text("""
                SELECT id, step_index, state, created_at
                FROM session_checkpoints
                WHERE session_id = :session_id
                ORDER BY step_index DESC
                LIMIT 1
            """),
            {"session_id": session_id},
        )
        r = row.fetchone()
        if r is None:
            return None
        d = dict(r._mapping)
        d["state"] = json.loads(d["state"]) if isinstance(d["state"], str) else d["state"]
        return d

    async def get_at_step(self, session_id: str, step_index: int) -> Optional[dict]:
        """获取指定步骤的检查点"""
        row = await self.db.execute(
            text("""
                SELECT id, step_index, state, created_at
                FROM session_checkpoints
                WHERE session_id = :session_id AND step_index = :step_index
                ORDER BY created_at DESC
                LIMIT 1
            """),
            {"session_id": session_id, "step_index": step_index},
        )
        r = row.fetchone()
        if r is None:
            return None
        d = dict(r._mapping)
        d["state"] = json.loads(d["state"]) if isinstance(d["state"], str) else d["state"]
        return d

    async def list_all(self, session_id: str) -> list[dict]:
        """列出所有检查点"""
        rows = await self.db.execute(
            text("""
                SELECT id, step_index, created_at
                FROM session_checkpoints
                WHERE session_id = :session_id
                ORDER BY step_index ASC
            """),
            {"session_id": session_id},
        )
        return [dict(r._mapping) for r in rows.fetchall()]

    async def fork(
        self, session_id: str, from_step: int, new_user_id: str = ""
    ) -> str:
        """从某个检查点分叉，创建新 session"""
        checkpoint = await self.get_at_step(session_id, from_step)
        if checkpoint is None:
            raise ValueError(f"No checkpoint at step {from_step} for session {session_id}")

        new_session_id = str(uuid.uuid4())

        # 获取原 session 信息
        row = await self.db.execute(
            text("SELECT user_id, agent_id, task, metadata FROM sessions WHERE id = :id"),
            {"id": session_id},
        )
        orig = row.fetchone()
        if orig is None:
            raise ValueError(f"Session not found: {session_id}")
        orig = dict(orig._mapping)

        # 创建新 session
        await self.db.execute(
            text("""
                INSERT INTO sessions (id, agent_id, user_id, task, status, current_step, metadata, created_at, updated_at)
                VALUES (:id, :agent_id, :user_id, :task, 'forked', :step, :meta, NOW(), NOW())
            """),
            {
                "id": new_session_id,
                "agent_id": orig["agent_id"],
                "user_id": new_user_id or orig["user_id"],
                "task": orig["task"],
                "step": from_step,
                "meta": json.dumps(orig.get("metadata", {}), ensure_ascii=False),
            },
        )

        # 复制检查点
        await self.db.execute(
            text("""
                INSERT INTO session_checkpoints (id, session_id, step_index, state, created_at)
                SELECT gen_random_uuid(), :new_id, step_index, state, NOW()
                FROM session_checkpoints
                WHERE session_id = :old_id AND step_index <= :step
            """),
            {"new_id": new_session_id, "old_id": session_id, "step": from_step},
        )
        await self.db.commit()

        logger.info(f"Session forked: {session_id} → {new_session_id} at step {from_step}")
        return new_session_id

    async def rollback(self, session_id: str, to_step: int) -> dict:
        """回滚到指定步骤的检查点"""
        checkpoint = await self.get_at_step(session_id, to_step)
        if checkpoint is None:
            raise ValueError(f"No checkpoint at step {to_step}")

        # 删除该步骤之后的所有检查点
        await self.db.execute(
            text("""
                DELETE FROM session_checkpoints
                WHERE session_id = :session_id AND step_index > :step
            """),
            {"session_id": session_id, "step": to_step},
        )

        # 更新 session 当前步骤
        await self.db.execute(
            text("""
                UPDATE sessions SET current_step = :step, status = 'rolled_back', updated_at = NOW()
                WHERE id = :id
            """),
            {"id": session_id, "step": to_step},
        )

        # 删除之后的步骤记录
        await self.db.execute(
            text("""
                DELETE FROM session_steps
                WHERE session_id = :session_id AND step_index > :step
            """),
            {"session_id": session_id, "step": to_step},
        )

        await self.db.commit()
        logger.info(f"Session rolled back: {session_id} to step {to_step}")
        return checkpoint["state"]

    async def replay(self, session_id: str) -> list[dict]:
        """重放整个 session 的所有步骤（确定性回放）"""
        rows = await self.db.execute(
            text("""
                SELECT step_index, type, content, tokens_used, latency_ms, error, created_at
                FROM session_steps
                WHERE session_id = :session_id
                ORDER BY step_index ASC
            """),
            {"session_id": session_id},
        )
        steps = []
        for r in rows.fetchall():
            d = dict(r._mapping)
            if isinstance(d.get("content"), str):
                try:
                    d["content"] = json.loads(d["content"])
                except (json.JSONDecodeError, TypeError):
                    pass
            steps.append(d)
        return steps

    async def snapshot(self, session_id: str) -> dict:
        """创建完整快照（当前状态 + 所有步骤 + 检查点）"""
        # 获取 session 信息
        row = await self.db.execute(
            text("SELECT * FROM sessions WHERE id = :id"),
            {"id": session_id},
        )
        session = row.fetchone()
        if session is None:
            raise ValueError(f"Session not found: {session_id}")
        session = dict(session._mapping)

        steps = await self.replay(session_id)
        checkpoints = await self.list_all(session_id)

        return {
            "session": session,
            "steps": steps,
            "checkpoints": checkpoints,
            "snapshot_at": datetime.now(timezone.utc).isoformat(),
        }

    async def cleanup(self, session_id: str):
        """清理 session 的所有检查点"""
        await self.db.execute(
            text("DELETE FROM session_checkpoints WHERE session_id = :id"),
            {"id": session_id},
        )
        await self.db.commit()
