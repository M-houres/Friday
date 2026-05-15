"""Audit domain operations."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any
import uuid

from sqlalchemy import text

from src.productization.base_service import _json_dumps
from src.productization.managed_config import managed_config_store


class AuditOpsMixin:
    async def get_ops_summary(self) -> dict:
        tables = {
            "users": "app_users",
            "templates": "prompt_templates",
            "knowledge_documents": "knowledge_documents",
            "product_records": "product_records",
            "result_records": "result_records",
            "async_jobs": "async_jobs",
            "billing_plans": "billing_plans",
            "payment_orders": "payment_orders",
            "entitlement_ledger": "entitlement_ledger",
            "ops_audit_logs": "ops_audit_logs",
        }
        summary: dict[str, Any] = {}
        for key, table in tables.items():
            result = await self._execute_or_empty(f"SELECT COUNT(*) FROM {table}")
            summary[key] = int(result.scalar() or 0) if result is not None else 0

        rows = await self._execute_or_empty(
            """
            SELECT status, COUNT(*) AS count
            FROM async_jobs
            GROUP BY status
            ORDER BY count DESC
            """
        )
        summary["jobs_by_status"] = (
            {str(row.status): int(row.count) for row in rows.fetchall()}
            if rows is not None
            else {}
        )
        credits = await self._execute_or_empty(
            """
            SELECT
                COALESCE(SUM(credits_balance), 0) AS balance,
                COALESCE(SUM(credits_granted_total), 0) AS granted,
                COALESCE(SUM(credits_used_total), 0) AS used
            FROM user_entitlements
            """
        )
        if credits is not None:
            row = credits.fetchone()
            if row is not None:
                summary["credits"] = {
                    "balance": int(row.balance or 0),
                    "granted": int(row.granted or 0),
                    "used": int(row.used or 0),
                }
        if "credits" not in summary:
            summary["credits"] = {"balance": 0, "granted": 0, "used": 0}
        return summary

    async def create_ops_audit_log(
        self,
        *,
        action: str,
        resource_type: str,
        resource_id: str,
        actor_user_id: str = "",
        target_user_id: str = "",
        status: str = "success",
        detail: dict | None = None,
    ) -> dict:
        log_id = uuid.uuid4().hex
        await self.db.execute(
            text(
                """
                INSERT INTO ops_audit_logs (
                    id, actor_user_id, action, resource_type, resource_id,
                    target_user_id, status, detail, created_at
                )
                VALUES (
                    :id, :actor_user_id, :action, :resource_type, :resource_id,
                    :target_user_id, :status, CAST(:detail AS JSONB), NOW()
                )
                """
            ),
            {
                "id": log_id,
                "actor_user_id": actor_user_id,
                "action": action,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "target_user_id": target_user_id,
                "status": status,
                "detail": _json_dumps(detail or {}),
            },
        )
        await self.db.commit()
        return {"audit_log_id": log_id, "action": action, "resource_type": resource_type, "resource_id": resource_id}

    async def list_ops_audit_logs(
        self,
        *,
        resource_type: str = "",
        resource_id: str = "",
        target_user_id: str = "",
        limit: int = 100,
    ) -> list[dict]:
        query = "SELECT * FROM ops_audit_logs WHERE 1=1"
        params: dict[str, Any] = {"limit": limit}
        if resource_type:
            query += " AND resource_type = :resource_type"
            params["resource_type"] = resource_type
        if resource_id:
            query += " AND resource_id = :resource_id"
            params["resource_id"] = resource_id
        if target_user_id:
            query += " AND target_user_id = :target_user_id"
            params["target_user_id"] = target_user_id
        query += " ORDER BY created_at DESC LIMIT :limit"
        return await self._fetch_all(query, params)


    async def publish_config_release(
        self,
        *,
        release_type: str,
        target_id: str,
        actor_user_id: str = "",
        version_label: str = "",
        change_note: str = "",
    ) -> dict:
        target_id = self._normalize_release_target_id(release_type, target_id)
        snapshot = await self._build_config_snapshot(release_type, target_id)
        release_id = uuid.uuid4().hex
        version = version_label or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        await self.db.execute(
            text(
                """
                INSERT INTO config_releases (
                    id, release_type, target_id, version_label, status,
                    snapshot, actor_user_id, change_note, rolled_back_from, created_at
                )
                VALUES (
                    :id, :release_type, :target_id, :version_label, 'published',
                    CAST(:snapshot AS JSONB), :actor_user_id, :change_note, '', NOW()
                )
                """
            ),
            {
                "id": release_id,
                "release_type": release_type,
                "target_id": target_id,
                "version_label": version,
                "snapshot": _json_dumps(snapshot),
                "actor_user_id": actor_user_id,
                "change_note": change_note,
            },
        )
        await self.db.commit()
        await self.create_ops_audit_log(
            actor_user_id=actor_user_id,
            action="publish_config_release",
            resource_type=release_type,
            resource_id=target_id,
            detail={"release_id": release_id, "version_label": version, "change_note": change_note},
        )
        return await self.get_config_release(release_id) or {"id": release_id}

    async def list_config_releases(self, release_type: str = "", target_id: str = "", limit: int = 100) -> list[dict]:
        query = "SELECT * FROM config_releases WHERE 1=1"
        params: dict[str, Any] = {"limit": limit}
        if release_type:
            query += " AND release_type = :release_type"
            params["release_type"] = release_type
        if target_id:
            query += " AND target_id = :target_id"
            params["target_id"] = target_id
        query += " ORDER BY created_at DESC LIMIT :limit"
        return await self._fetch_all(query, params)

    async def get_config_release(self, release_id: str) -> dict | None:
        return await self._fetch_one_or_none("SELECT * FROM config_releases WHERE id = :id", {"id": release_id})

    async def rollback_config_release(
        self,
        release_id: str,
        *,
        actor_user_id: str = "",
        change_note: str = "",
    ) -> dict | None:
        release = await self.get_config_release(release_id)
        if release is None:
            return None
        snapshot = deepcopy(release.get("snapshot") or {})
        release_type = str(release.get("release_type") or "")
        target_id = self._normalize_release_target_id(release_type, str(release.get("target_id") or ""))
        await self._restore_config_snapshot(release_type, target_id, snapshot)
        rollback_id = uuid.uuid4().hex
        await self.db.execute(
            text(
                """
                INSERT INTO config_releases (
                    id, release_type, target_id, version_label, status,
                    snapshot, actor_user_id, change_note, rolled_back_from, created_at
                )
                VALUES (
                    :id, :release_type, :target_id, :version_label, 'rolled_back',
                    CAST(:snapshot AS JSONB), :actor_user_id, :change_note, :rolled_back_from, NOW()
                )
                """
            ),
            {
                "id": rollback_id,
                "release_type": release["release_type"],
                "target_id": release["target_id"],
                "version_label": f"rollback-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
                "snapshot": _json_dumps(snapshot),
                "actor_user_id": actor_user_id,
                "change_note": change_note,
                "rolled_back_from": release_id,
            },
        )
        await self.db.commit()
        await self.create_ops_audit_log(
            actor_user_id=actor_user_id,
            action="rollback_config_release",
            resource_type=str(release.get("release_type") or ""),
            resource_id=str(release.get("target_id") or ""),
            detail={"release_id": release_id, "rollback_id": rollback_id, "change_note": change_note},
        )
        return await self.get_config_release(rollback_id)

    async def _build_config_snapshot(self, release_type: str, target_id: str) -> dict:
        if release_type == "template":
            item = await self.get_template(target_id)
            if item is None:
                raise ValueError("CONFIG_TARGET_NOT_FOUND")
            return item
        if release_type == "knowledge":
            item = await self.get_knowledge_document(target_id)
            if item is None:
                raise ValueError("CONFIG_TARGET_NOT_FOUND")
            return item
        if release_type == "system_config":
            return managed_config_store.get_system_settings()
        if release_type == "model_strategy":
            return managed_config_store.get_model_strategy()
        raise ValueError("UNSUPPORTED_RELEASE_TYPE")

    async def _restore_config_snapshot(self, release_type: str, target_id: str, snapshot: dict):
        if release_type == "template":
            await self.create_template(
                str(snapshot.get("name") or ""),
                str(snapshot.get("content") or ""),
                template_id=target_id,
                category=str(snapshot.get("category") or "general"),
                project_id=str(snapshot.get("project_id") or ""),
                scope=str(snapshot.get("scope") or "project"),
                variables=list(snapshot.get("variables") or []),
                metadata=dict(snapshot.get("metadata") or {}),
            )
            return
        if release_type == "knowledge":
            await self.create_knowledge_document(
                str(snapshot.get("title") or ""),
                str(snapshot.get("content") or ""),
                project_id=str(snapshot.get("project_id") or ""),
                document_id=target_id,
                doc_type=str(snapshot.get("doc_type") or "note"),
                tags=list(snapshot.get("tags") or []),
                metadata=dict(snapshot.get("metadata") or {}),
            )
            return
        if release_type == "system_config":
            managed_config_store.update_system_settings(dict(snapshot or {}))
            return
        if release_type == "model_strategy":
            managed_config_store.update_model_strategy(dict(snapshot or {}))
            return
        raise ValueError("UNSUPPORTED_RELEASE_TYPE")

    @staticmethod
    def _normalize_release_target_id(release_type: str, target_id: str) -> str:
        if release_type in {"system_config", "model_strategy"}:
            return target_id or "global"
        return target_id

