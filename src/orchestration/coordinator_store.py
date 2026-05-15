"""Coordinator 的工作流/节点持久化辅助。"""

from __future__ import annotations

import json
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.orchestration.dag import DAG, DAGNode


class CoordinatorStore:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def initialize_workflow(
        self,
        workflow_id: str,
        *,
        user_id: str,
        task: str,
        coordinator_id: str,
        resume_existing: bool,
    ):
        if resume_existing:
            await self.db.execute(
                text(
                    """
                    UPDATE agent_workflows
                    SET user_id = :user_id,
                        task = :task,
                        status = 'planning',
                        coordinator_id = :coord_id,
                        heartbeat_at = NOW(),
                        completed_at = NULL,
                        error = NULL
                    WHERE id = :id
                    """
                ),
                {"id": workflow_id, "user_id": user_id, "task": task, "coord_id": coordinator_id},
            )
        else:
            await self.db.execute(
                text(
                    """
                    INSERT INTO agent_workflows (id, user_id, task, status, coordinator_id, heartbeat_at, started_at)
                    VALUES (:id, :user_id, :task, 'planning', :coord_id, NOW(), NOW())
                    """
                ),
                {"id": workflow_id, "user_id": user_id, "task": task, "coord_id": coordinator_id},
            )
        await self.db.commit()

    async def save_plan(self, workflow_id: str, dag: DAG):
        await self.db.execute(
            text(
                """
                UPDATE agent_workflows
                SET plan = :plan,
                    status = 'dispatching',
                    heartbeat_at = NOW()
                WHERE id = :id
                """
            ),
            {"id": workflow_id, "plan": json.dumps(dag.to_dict(), ensure_ascii=False)},
        )
        await self.db.commit()

    async def update_workflow_status(self, workflow_id: str, status: str):
        await self.db.execute(
            text("UPDATE agent_workflows SET status = :status, heartbeat_at = NOW() WHERE id = :id"),
            {"id": workflow_id, "status": status},
        )
        await self.db.commit()

    async def complete_workflow(
        self,
        workflow_id: str,
        *,
        status: str,
        result: dict,
        degradation_level: int,
    ):
        await self.db.execute(
            text(
                """
                UPDATE agent_workflows
                SET status = :status,
                    result = :result,
                    degradation_level = :level,
                    completed_at = NOW()
                WHERE id = :id
                """
            ),
            {
                "id": workflow_id,
                "status": status,
                "result": json.dumps(result, ensure_ascii=False, default=str),
                "level": degradation_level,
            },
        )
        await self.db.commit()

    async def fail_workflow(self, workflow_id: str, error: str):
        await self.db.execute(
            text(
                """
                UPDATE agent_workflows
                SET status = 'failed',
                    error = :error,
                    completed_at = NOW()
                WHERE id = :id
                """
            ),
            {"id": workflow_id, "error": error},
        )
        await self.db.commit()

    async def start_node(self, workflow_id: str, node: DAGNode) -> str:
        node_db_id = uuid.uuid4().hex
        await self.db.execute(
            text(
                """
                INSERT INTO workflow_nodes (id, workflow_id, node_id, task, dependencies, status, started_at)
                VALUES (:id, :wf_id, :node_id, :task, :deps, 'running', NOW())
                """
            ),
            {
                "id": node_db_id,
                "wf_id": workflow_id,
                "node_id": node.node_id,
                "task": node.task,
                "deps": node.dependencies,
            },
        )
        await self.db.commit()
        return node_db_id

    async def complete_node(self, node_db_id: str, result: dict, model: str):
        payload = dict(result)
        tokens = payload.pop("_tokens", 0)
        payload.pop("_model", None)
        await self.db.execute(
            text(
                """
                UPDATE workflow_nodes
                SET status = :status,
                    result = :result,
                    model = :model,
                    tokens_used = :tokens,
                    completed_at = NOW()
                WHERE id = :id
                """
            ),
            {
                "id": node_db_id,
                "status": "completed",
                "result": json.dumps(payload, ensure_ascii=False),
                "model": model,
                "tokens": tokens,
            },
        )
        await self.db.commit()

