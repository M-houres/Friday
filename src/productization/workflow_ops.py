"""Workflow, approvals, and async job domain operations."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import uuid

from sqlalchemy import text

from src.productization.base_service import UNSET, _datetime_from_timestamp, _json_dumps


class WorkflowOpsMixin:
    async def retry_async_job(
        self,
        job_id: str,
        *,
        actor_user_id: str = "",
    ) -> dict | None:
        job = await self.get_async_job(job_id)
        if job is None:
            return None
        payload = dict(job.get("payload") or {})
        new_job = await self.create_async_job(
            uuid.uuid4().hex,
            str(job.get("job_type") or "workflow"),
            payload,
            priority=int(job.get("priority") or 5),
            status="queued",
        )
        await self.create_ops_audit_log(
            actor_user_id=actor_user_id,
            action="retry_job",
            resource_type="async_job",
            resource_id=job_id,
            detail={"new_job_id": new_job.get("job_id")},
        )
        return new_job

    async def refund_workflow_charge(
        self,
        workflow_id: str,
        *,
        actor_user_id: str = "",
        reason: str = "",
    ) -> dict | None:
        record = await self.get_result_record(workflow_id)
        if record is None:
            return None
        normalized = dict(record.get("normalized_result") or {})
        billing = dict(normalized.get("billing") or {})
        credits_cost = int(billing.get("credits_cost") or 0)
        if credits_cost <= 0:
            raise ValueError("WORKFLOW_NOT_CHARGEABLE")
        if billing.get("refunded"):
            raise ValueError("WORKFLOW_ALREADY_REFUNDED")

        user_id = str(record.get("user_id") or "default")
        await self.refund_user_credits(
            user_id,
            credits_cost,
            reason=reason or "workflow_refund",
            source_type="workflow_refund",
            source_id=workflow_id,
            operator_user_id=actor_user_id,
        )
        billing["refunded"] = True
        billing["refunded_at"] = datetime.now(timezone.utc).isoformat()
        billing["refund_reason"] = reason
        normalized["billing"] = billing
        await self.db.execute(
            text(
                """
                UPDATE result_records
                SET normalized_result = CAST(:normalized_result AS JSONB),
                    updated_at = NOW()
                WHERE workflow_id = :workflow_id
                """
            ),
            {"workflow_id": workflow_id, "normalized_result": _json_dumps(normalized)},
        )
        await self.db.commit()
        await self.create_ops_audit_log(
            actor_user_id=actor_user_id,
            action="refund_workflow_charge",
            resource_type="workflow",
            resource_id=workflow_id,
            target_user_id=user_id,
            detail={"credits_cost": credits_cost, "reason": reason},
        )
        return {"workflow_id": workflow_id, "user_id": user_id, "credits_refunded": credits_cost, "billing": billing}

    async def create_async_job(
        self,
        job_id: str,
        job_type: str,
        payload: dict,
        *,
        priority: int = 5,
        status: str = "queued",
        result: Any = None,
        error: str = "",
        created_at: float | int | None = None,
        started_at: float | int | None = None,
        completed_at: float | int | None = None,
    ) -> dict:
        await self.db.execute(
            text(
                """
                INSERT INTO async_jobs (id, job_type, status, priority, payload, result, error, worker_name, attempts, created_at, started_at, heartbeat_at, completed_at)
                VALUES (
                    :id, :job_type, :status, :priority,
                    CAST(:payload AS JSONB),
                    CAST(:result AS JSONB),
                    :error,
                    '',
                    0,
                    COALESCE(:created_at, NOW()),
                    :started_at,
                    :heartbeat_at,
                    :completed_at
                )
                ON CONFLICT (id) DO UPDATE
                SET job_type = EXCLUDED.job_type,
                    status = EXCLUDED.status,
                    priority = EXCLUDED.priority,
                    payload = EXCLUDED.payload,
                    result = EXCLUDED.result,
                    error = EXCLUDED.error,
                    started_at = EXCLUDED.started_at,
                    heartbeat_at = EXCLUDED.heartbeat_at,
                    completed_at = EXCLUDED.completed_at
                """
            ),
            {
                "id": job_id,
                "job_type": job_type,
                "status": status,
                "priority": priority,
                "payload": _json_dumps(payload),
                "result": _json_dumps(result) if result is not None else "null",
                "error": error,
                "created_at": _datetime_from_timestamp(created_at),
                "started_at": _datetime_from_timestamp(started_at),
                "heartbeat_at": _datetime_from_timestamp(started_at or created_at),
                "completed_at": _datetime_from_timestamp(completed_at),
            },
        )
        await self.db.commit()
        return {"job_id": job_id, "status": status, "priority": priority}

    async def claim_next_async_job(self, worker_name: str) -> dict | None:
        row = await self.db.execute(
            text(
                """
                WITH picked AS (
                    SELECT id
                    FROM async_jobs
                    WHERE status = 'queued'
                    ORDER BY priority ASC, created_at ASC
                    LIMIT 1
                    FOR UPDATE SKIP LOCKED
                )
                UPDATE async_jobs
                SET status = 'running',
                    error = '',
                    worker_name = :worker_name,
                    started_at = NOW(),
                    heartbeat_at = NOW(),
                    attempts = attempts + 1
                WHERE id IN (SELECT id FROM picked)
                RETURNING *
                """
            ),
            {"worker_name": worker_name},
        )
        job = row.fetchone()
        await self.db.commit()
        return dict(job._mapping) if job else None

    async def update_async_job(
        self,
        job_id: str,
        *,
        status: str | None = None,
        priority: int | None = None,
        payload: dict | object = UNSET,
        result: Any = UNSET,
        error: str | object = UNSET,
        worker_name: str | object = UNSET,
        started_at: float | int | None | object = UNSET,
        heartbeat_at: float | int | None | object = UNSET,
        completed_at: float | int | None | object = UNSET,
    ) -> dict | None:
        updates: list[str] = []
        params: dict[str, Any] = {"id": job_id}

        if status is not None:
            updates.append("status = :status")
            params["status"] = status
        if priority is not None:
            updates.append("priority = :priority")
            params["priority"] = priority
        if payload is not UNSET:
            updates.append("payload = CAST(:payload AS JSONB)")
            params["payload"] = _json_dumps(payload)
        if result is not UNSET:
            updates.append("result = CAST(:result AS JSONB)")
            params["result"] = _json_dumps(result) if result is not None else "null"
        if error is not UNSET:
            updates.append("error = :error")
            params["error"] = error
        if worker_name is not UNSET:
            updates.append("worker_name = :worker_name")
            params["worker_name"] = worker_name
        if started_at is not UNSET:
            updates.append("started_at = :started_at")
            params["started_at"] = _datetime_from_timestamp(started_at)
        if heartbeat_at is not UNSET:
            updates.append("heartbeat_at = :heartbeat_at")
            params["heartbeat_at"] = _datetime_from_timestamp(heartbeat_at)
        if completed_at is not UNSET:
            updates.append("completed_at = :completed_at")
            params["completed_at"] = _datetime_from_timestamp(completed_at)

        if updates:
            await self.db.execute(
                text(f"UPDATE async_jobs SET {', '.join(updates)} WHERE id = :id"),
                params,
            )
            await self.db.commit()
        return await self.get_async_job(job_id)

    async def get_async_job(self, job_id: str, user_id: str = "") -> dict | None:
        query = "SELECT * FROM async_jobs WHERE id = :id"
        params: dict[str, Any] = {"id": job_id}
        if user_id:
            query += " AND payload->>'user_id' = :user_id"
            params["user_id"] = user_id
        return await self._fetch_one_or_none(query, params)

    async def list_async_jobs(self, status: str = "", user_id: str = "", limit: int = 100) -> list[dict]:
        query = "SELECT * FROM async_jobs WHERE 1=1"
        params: dict[str, Any] = {"limit": limit}
        if status:
            query += " AND status = :status"
            params["status"] = status
        if user_id:
            query += " AND payload->>'user_id' = :user_id"
            params["user_id"] = user_id
        query += " ORDER BY created_at DESC LIMIT :limit"
        return await self._fetch_all(query, params)

    async def recover_async_jobs(self) -> list[dict]:
        rows = await self._execute_or_empty(
            """
            SELECT * FROM async_jobs
            WHERE status IN ('queued', 'running')
            ORDER BY created_at ASC
            """
        )
        if rows is None:
            return []
        jobs = [dict(row._mapping) for row in rows.fetchall()]
        if jobs:
            await self.db.execute(
                text(
                    """
                    UPDATE async_jobs
                    SET status = 'queued',
                        error = '',
                        worker_name = '',
                        started_at = NULL,
                        heartbeat_at = NULL,
                        completed_at = NULL
                    WHERE status IN ('queued', 'running')
                    """
                )
            )
            await self.db.commit()
        return jobs

    async def heartbeat_async_job(self, job_id: str, worker_name: str) -> dict | None:
        await self.db.execute(
            text(
                """
                UPDATE async_jobs
                SET heartbeat_at = NOW(),
                    worker_name = :worker_name
                WHERE id = :id
                """
            ),
            {"id": job_id, "worker_name": worker_name},
        )
        await self.db.commit()
        return await self.get_async_job(job_id)

    async def recycle_stale_async_jobs(self, stale_after_s: int) -> int:
        result = await self.db.execute(
            text(
                """
                UPDATE async_jobs
                SET status = 'queued',
                    worker_name = '',
                    error = 'Worker heartbeat stale; recycled',
                    started_at = NULL,
                    heartbeat_at = NULL,
                    completed_at = NULL
                WHERE status = 'running'
                  AND heartbeat_at IS NOT NULL
                  AND heartbeat_at < NOW() - (:stale_after_s || ' seconds')::interval
                """
            ),
            {"stale_after_s": stale_after_s},
        )
        await self.db.commit()
        return int(result.rowcount or 0)

    async def create_approval_request(
        self,
        workflow_id: str,
        step_id: str,
        title: str,
        *,
        project_id: str = "",
        page_id: str = "",
        requester_user_id: str = "default",
        detail: dict | None = None,
    ) -> dict:
        approval_id = uuid.uuid4().hex
        await self.db.execute(
            text(
                """
                INSERT INTO approval_requests (
                    id, workflow_id, project_id, page_id, step_id, title, detail,
                    status, requester_user_id, reviewer_user_id, review_comment, created_at
                )
                VALUES (
                    :id, :workflow_id, :project_id, :page_id, :step_id, :title, CAST(:detail AS JSONB),
                    'pending', :requester_user_id, '', '', NOW()
                )
                """
            ),
            {
                "id": approval_id,
                "workflow_id": workflow_id,
                "project_id": project_id,
                "page_id": page_id,
                "step_id": step_id,
                "title": title,
                "detail": _json_dumps(detail or {}),
                "requester_user_id": requester_user_id,
            },
        )
        await self.db.commit()
        return {"approval_id": approval_id, "workflow_id": workflow_id, "step_id": step_id, "status": "pending"}

    async def attach_approval_checkpoint(
        self,
        approval_id: str,
        *,
        scenario_state: dict,
        scenario_outputs: dict,
        next_step_index: int,
    ) -> dict | None:
        approval = await self.get_approval_request(approval_id)
        if approval is None:
            return None
        detail = dict(approval.get("detail") or {})
        detail["checkpoint"] = {
            "scenario_state": scenario_state,
            "scenario_outputs": scenario_outputs,
            "next_step_index": next_step_index,
        }
        await self.db.execute(
            text(
                """
                UPDATE approval_requests
                SET detail = CAST(:detail AS JSONB)
                WHERE id = :id
                """
            ),
            {"id": approval_id, "detail": _json_dumps(detail)},
        )
        await self.db.commit()
        return await self.get_approval_request(approval_id)

    async def list_approval_requests(
        self,
        status: str = "",
        project_id: str = "",
        page_id: str = "",
        requester_user_id: str = "",
        workflow_id: str = "",
        limit: int = 100,
    ) -> list[dict]:
        query = "SELECT * FROM approval_requests WHERE 1=1"
        params: dict[str, Any] = {"limit": limit}
        if status:
            query += " AND status = :status"
            params["status"] = status
        if project_id:
            query += " AND project_id = :project_id"
            params["project_id"] = project_id
        if page_id:
            query += " AND page_id = :page_id"
            params["page_id"] = page_id
        if requester_user_id:
            query += " AND requester_user_id = :requester_user_id"
            params["requester_user_id"] = requester_user_id
        if workflow_id:
            query += " AND workflow_id = :workflow_id"
            params["workflow_id"] = workflow_id
        query += " ORDER BY created_at DESC LIMIT :limit"
        return await self._fetch_all(query, params)

    async def get_approval_request(self, approval_id: str) -> dict | None:
        return await self._fetch_one_or_none(
            "SELECT * FROM approval_requests WHERE id = :id",
            {"id": approval_id},
        )

    async def review_approval_request(
        self,
        approval_id: str,
        *,
        approved: bool,
        reviewer_user_id: str,
        comment: str = "",
    ) -> dict | None:
        status = "approved" if approved else "rejected"
        await self.db.execute(
            text(
                """
                UPDATE approval_requests
                SET status = :status,
                    reviewer_user_id = :reviewer_user_id,
                    review_comment = :comment,
                    reviewed_at = NOW()
                WHERE id = :id
                """
            ),
            {
                "id": approval_id,
                "status": status,
                "reviewer_user_id": reviewer_user_id,
                "comment": comment,
            },
        )
        await self.db.commit()
        return await self.get_approval_request(approval_id)
