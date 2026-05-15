"""Support and risk domain operations."""

from __future__ import annotations

from typing import Any
import uuid

from sqlalchemy import text

from src.productization.base_service import _json_dumps


class SupportOpsMixin:
    async def create_support_ticket(
        self,
        *,
        user_id: str,
        ticket_type: str,
        title: str,
        detail: dict | None = None,
        priority: str = "normal",
        metadata: dict | None = None,
    ) -> dict:
        ticket_id = uuid.uuid4().hex
        await self.db.execute(
            text(
                """
                INSERT INTO support_tickets (
                    id, user_id, ticket_type, title, detail, status, priority,
                    assignee_user_id, resolution, metadata, created_at, updated_at
                )
                VALUES (
                    :id, :user_id, :ticket_type, :title, CAST(:detail AS JSONB), 'open', :priority,
                    '', '', CAST(:metadata AS JSONB), NOW(), NOW()
                )
                """
            ),
            {
                "id": ticket_id,
                "user_id": user_id,
                "ticket_type": ticket_type,
                "title": title,
                "detail": _json_dumps(detail or {}),
                "priority": priority,
                "metadata": _json_dumps(metadata or {}),
            },
        )
        await self.db.commit()
        return await self.get_support_ticket(ticket_id) or {"id": ticket_id}

    async def get_support_ticket(self, ticket_id: str) -> dict | None:
        return await self._fetch_one_or_none("SELECT * FROM support_tickets WHERE id = :id", {"id": ticket_id})

    async def list_support_tickets(self, status: str = "", user_id: str = "", limit: int = 100) -> list[dict]:
        query = "SELECT * FROM support_tickets WHERE 1=1"
        params: dict[str, Any] = {"limit": limit}
        if status:
            query += " AND status = :status"
            params["status"] = status
        if user_id:
            query += " AND user_id = :user_id"
            params["user_id"] = user_id
        query += " ORDER BY updated_at DESC LIMIT :limit"
        return await self._fetch_all(query, params)

    async def update_support_ticket(
        self,
        ticket_id: str,
        *,
        status: str = "",
        assignee_user_id: str = "",
        resolution: str = "",
        actor_user_id: str = "",
    ) -> dict | None:
        ticket = await self.get_support_ticket(ticket_id)
        if ticket is None:
            return None
        await self.db.execute(
            text(
                """
                UPDATE support_tickets
                SET status = COALESCE(NULLIF(:status, ''), status),
                    assignee_user_id = COALESCE(NULLIF(:assignee_user_id, ''), assignee_user_id),
                    resolution = COALESCE(NULLIF(:resolution, ''), resolution),
                    updated_at = NOW(),
                    resolved_at = CASE WHEN :status = 'resolved' THEN NOW() ELSE resolved_at END
                WHERE id = :id
                """
            ),
            {
                "id": ticket_id,
                "status": status,
                "assignee_user_id": assignee_user_id,
                "resolution": resolution,
            },
        )
        await self.db.commit()
        await self.create_ops_audit_log(
            actor_user_id=actor_user_id,
            action="update_support_ticket",
            resource_type="support_ticket",
            resource_id=ticket_id,
            target_user_id=str(ticket.get("user_id") or ""),
            detail={"status": status, "resolution": resolution},
        )
        return await self.get_support_ticket(ticket_id)

    async def create_appeal_record(
        self,
        *,
        user_id: str,
        appeal_type: str,
        title: str,
        detail: dict | None = None,
        related_resource_type: str = "",
        related_resource_id: str = "",
        metadata: dict | None = None,
    ) -> dict:
        appeal_id = uuid.uuid4().hex
        await self.db.execute(
            text(
                """
                INSERT INTO appeal_records (
                    id, user_id, appeal_type, title, detail, status,
                    related_resource_type, related_resource_id, reviewer_user_id,
                    decision_note, metadata, created_at, updated_at
                )
                VALUES (
                    :id, :user_id, :appeal_type, :title, CAST(:detail AS JSONB), 'pending',
                    :related_resource_type, :related_resource_id, '',
                    '', CAST(:metadata AS JSONB), NOW(), NOW()
                )
                """
            ),
            {
                "id": appeal_id,
                "user_id": user_id,
                "appeal_type": appeal_type,
                "title": title,
                "detail": _json_dumps(detail or {}),
                "related_resource_type": related_resource_type,
                "related_resource_id": related_resource_id,
                "metadata": _json_dumps(metadata or {}),
            },
        )
        await self.db.commit()
        return await self.get_appeal_record(appeal_id) or {"id": appeal_id}

    async def get_appeal_record(self, appeal_id: str) -> dict | None:
        return await self._fetch_one_or_none("SELECT * FROM appeal_records WHERE id = :id", {"id": appeal_id})

    async def list_appeal_records(self, status: str = "", user_id: str = "", limit: int = 100) -> list[dict]:
        query = "SELECT * FROM appeal_records WHERE 1=1"
        params: dict[str, Any] = {"limit": limit}
        if status:
            query += " AND status = :status"
            params["status"] = status
        if user_id:
            query += " AND user_id = :user_id"
            params["user_id"] = user_id
        query += " ORDER BY updated_at DESC LIMIT :limit"
        return await self._fetch_all(query, params)

    async def review_appeal_record(
        self,
        appeal_id: str,
        *,
        approved: bool,
        reviewer_user_id: str = "",
        decision_note: str = "",
    ) -> dict | None:
        appeal = await self.get_appeal_record(appeal_id)
        if appeal is None:
            return None
        status = "approved" if approved else "rejected"
        await self.db.execute(
            text(
                """
                UPDATE appeal_records
                SET status = :status,
                    reviewer_user_id = :reviewer_user_id,
                    decision_note = :decision_note,
                    updated_at = NOW(),
                    reviewed_at = NOW()
                WHERE id = :id
                """
            ),
            {
                "id": appeal_id,
                "status": status,
                "reviewer_user_id": reviewer_user_id,
                "decision_note": decision_note,
            },
        )
        await self.db.commit()
        await self.create_ops_audit_log(
            actor_user_id=reviewer_user_id,
            action="review_appeal",
            resource_type="appeal_record",
            resource_id=appeal_id,
            target_user_id=str(appeal.get("user_id") or ""),
            detail={"approved": approved, "decision_note": decision_note},
        )
        return await self.get_appeal_record(appeal_id)


    async def create_risk_case(
        self,
        *,
        user_id: str,
        case_type: str,
        title: str,
        detail: dict | None = None,
        severity: str = "medium",
        related_resource_type: str = "",
        related_resource_id: str = "",
        metadata: dict | None = None,
    ) -> dict:
        case_id = uuid.uuid4().hex
        await self.db.execute(
            text(
                """
                INSERT INTO risk_cases (
                    id, user_id, case_type, title, detail, status, severity,
                    related_resource_type, related_resource_id, assignee_user_id,
                    resolution, metadata, created_at, updated_at
                )
                VALUES (
                    :id, :user_id, :case_type, :title, CAST(:detail AS JSONB), 'open', :severity,
                    :related_resource_type, :related_resource_id, '',
                    '', CAST(:metadata AS JSONB), NOW(), NOW()
                )
                """
            ),
            {
                "id": case_id,
                "user_id": user_id,
                "case_type": case_type,
                "title": title,
                "detail": _json_dumps(detail or {}),
                "severity": severity,
                "related_resource_type": related_resource_type,
                "related_resource_id": related_resource_id,
                "metadata": _json_dumps(metadata or {}),
            },
        )
        await self.db.commit()
        return await self.get_risk_case(case_id) or {"id": case_id}

    async def get_risk_case(self, case_id: str) -> dict | None:
        return await self._fetch_one_or_none("SELECT * FROM risk_cases WHERE id = :id", {"id": case_id})

    async def list_risk_cases(self, status: str = "", user_id: str = "", limit: int = 100) -> list[dict]:
        query = "SELECT * FROM risk_cases WHERE 1=1"
        params: dict[str, Any] = {"limit": limit}
        if status:
            query += " AND status = :status"
            params["status"] = status
        if user_id:
            query += " AND user_id = :user_id"
            params["user_id"] = user_id
        query += " ORDER BY updated_at DESC LIMIT :limit"
        return await self._fetch_all(query, params)

    async def update_risk_case(
        self,
        case_id: str,
        *,
        status: str = "",
        assignee_user_id: str = "",
        resolution: str = "",
        actor_user_id: str = "",
    ) -> dict | None:
        risk_case = await self.get_risk_case(case_id)
        if risk_case is None:
            return None
        await self.db.execute(
            text(
                """
                UPDATE risk_cases
                SET status = COALESCE(NULLIF(:status, ''), status),
                    assignee_user_id = COALESCE(NULLIF(:assignee_user_id, ''), assignee_user_id),
                    resolution = COALESCE(NULLIF(:resolution, ''), resolution),
                    updated_at = NOW(),
                    resolved_at = CASE WHEN :status = 'resolved' THEN NOW() ELSE resolved_at END
                WHERE id = :id
                """
            ),
            {
                "id": case_id,
                "status": status,
                "assignee_user_id": assignee_user_id,
                "resolution": resolution,
            },
        )
        await self.db.commit()
        await self.create_ops_audit_log(
            actor_user_id=actor_user_id,
            action="update_risk_case",
            resource_type="risk_case",
            resource_id=case_id,
            target_user_id=str(risk_case.get("user_id") or ""),
            detail={"status": status, "resolution": resolution},
        )
        return await self.get_risk_case(case_id)

