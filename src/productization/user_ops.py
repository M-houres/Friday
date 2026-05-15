"""User and account domain operations."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
import uuid

from sqlalchemy import text

from src.api.auth import hash_password, verify_password
from src.productization.base_service import UNSET


class UserOpsMixin:
    async def upsert_user(
        self,
        user_id: str,
        name: str = "",
        email: str = "",
        roles: list[str] | None = None,
        status: str = "active",
        metadata: dict | None = None,
        password_hash: str | object = UNSET,
        password_salt: str | object = UNSET,
        email_verified: bool | object = UNSET,
        last_login_at: datetime | None | object = UNSET,
    ) -> dict:
        existing = await self._fetch_one_or_none("SELECT * FROM app_users WHERE id = :id", {"id": user_id}) or {}
        payload = {
            "id": user_id,
            "name": name or user_id,
            "email": self._normalize_email(email),
            "status": status,
            "password_hash": existing.get("password_hash", "") if password_hash is UNSET else str(password_hash or ""),
            "password_salt": existing.get("password_salt", "") if password_salt is UNSET else str(password_salt or ""),
            "email_verified": (
                bool(existing.get("email_verified", False))
                if email_verified is UNSET
                else bool(email_verified)
            ),
            "last_login_at": (
                existing.get("last_login_at")
                if last_login_at is UNSET
                else last_login_at
            ),
            "metadata": json.dumps(metadata or {}, ensure_ascii=False),
            "roles": roles or ["builder"],
        }
        await self.db.execute(
            text(
                """
                INSERT INTO app_users (
                    id, name, email, status, password_hash, password_salt,
                    email_verified, last_login_at, metadata, created_at, updated_at
                )
                VALUES (
                    :id, :name, :email, :status, :password_hash, :password_salt,
                    :email_verified, :last_login_at, CAST(:metadata AS JSONB), NOW(), NOW()
                )
                ON CONFLICT (id) DO UPDATE
                SET name = EXCLUDED.name,
                    email = EXCLUDED.email,
                    status = EXCLUDED.status,
                    password_hash = EXCLUDED.password_hash,
                    password_salt = EXCLUDED.password_salt,
                    email_verified = EXCLUDED.email_verified,
                    last_login_at = EXCLUDED.last_login_at,
                    metadata = EXCLUDED.metadata,
                    updated_at = NOW()
                """
            ),
            payload,
        )
        await self.db.execute(text("DELETE FROM app_user_roles WHERE user_id = :id"), {"id": user_id})
        for role in payload["roles"]:
            await self.db.execute(
                text(
                    """
                    INSERT INTO app_user_roles (id, user_id, role_name, created_at)
                    VALUES (:id, :user_id, :role_name, NOW())
                    """
                ),
                {"id": uuid.uuid4().hex, "user_id": user_id, "role_name": role},
            )
        await self.db.commit()
        return {"user_id": user_id, "roles": payload["roles"], "status": status}

    async def list_users(self) -> list[dict]:
        return await self._fetch_all(
            """
            SELECT u.id, u.name, u.email, u.status, u.metadata, u.created_at, u.last_login_at,
                   CASE WHEN COALESCE(u.password_hash, '') <> '' THEN TRUE ELSE FALSE END AS has_password,
                   e.subscription_status,
                   e.credits_balance,
                   e.credits_granted_total,
                   e.credits_used_total,
                   p.name AS active_plan_name,
                   COALESCE(array_agg(r.role_name) FILTER (WHERE r.role_name IS NOT NULL), '{}') AS roles
            FROM app_users u
            LEFT JOIN app_user_roles r ON r.user_id = u.id
            LEFT JOIN user_entitlements e ON e.user_id = u.id
            LEFT JOIN billing_plans p ON p.id = e.active_plan_id
            GROUP BY u.id, e.subscription_status, e.credits_balance, e.credits_granted_total,
                     e.credits_used_total, p.name
            ORDER BY u.created_at DESC
            """
        )

    async def get_user_account(self, user_id: str) -> dict | None:
        user = await self._fetch_one_or_none("SELECT * FROM app_users WHERE id = :id", {"id": user_id})
        if user is None:
            return None
        roles = await self._get_user_roles(user_id)
        return {
            "user_id": str(user.get("id") or ""),
            "name": str(user.get("name") or ""),
            "email": str(user.get("email") or ""),
            "status": str(user.get("status") or "active"),
            "roles": roles,
            "metadata": dict(user.get("metadata") or {}),
            "email_verified": bool(user.get("email_verified", False)),
            "has_password": bool(user.get("password_hash")),
            "last_login_at": user.get("last_login_at"),
            "created_at": user.get("created_at"),
            "updated_at": user.get("updated_at"),
        }

    async def get_user_by_email(self, email: str) -> dict | None:
        normalized_email = self._normalize_email(email)
        if not normalized_email:
            return None
        return await self._fetch_one_or_none(
            "SELECT * FROM app_users WHERE email = :email",
            {"email": normalized_email},
        )

    async def register_user(
        self,
        *,
        email: str,
        password: str,
        name: str = "",
        metadata: dict | None = None,
    ) -> dict:
        normalized_email = self._normalize_email(email)
        if not normalized_email:
            raise ValueError("EMAIL_REQUIRED")
        existing = await self.get_user_by_email(normalized_email)
        if existing is not None:
            raise ValueError("EMAIL_ALREADY_EXISTS")
        users_result = await self._execute_or_empty("SELECT COUNT(*) FROM app_users")
        is_first_user = bool(users_result is not None and int(users_result.scalar() or 0) == 0)
        password_hash, password_salt = hash_password(password)
        user_id = uuid.uuid4().hex
        roles = ["admin", "operator", "builder"] if is_first_user else ["builder"]
        await self.upsert_user(
            user_id,
            name=name or normalized_email.split("@", 1)[0],
            email=normalized_email,
            roles=roles,
            status="active",
            metadata=metadata,
            password_hash=password_hash,
            password_salt=password_salt,
            email_verified=False,
        )
        return await self.get_user_account(user_id) or {"user_id": user_id, "email": normalized_email, "roles": roles}

    async def authenticate_user(self, *, email: str, password: str) -> dict | None:
        normalized_email = self._normalize_email(email)
        user = await self.get_user_by_email(normalized_email)
        if user is None:
            return None
        if str(user.get("status") or "active") != "active":
            raise ValueError("USER_DISABLED")
        if not verify_password(password, str(user.get("password_salt") or ""), str(user.get("password_hash") or "")):
            return None
        user_id = str(user.get("id") or "")
        await self.upsert_user(
            user_id,
            name=str(user.get("name") or user_id),
            email=normalized_email,
            roles=await self._get_user_roles(user_id),
            status=str(user.get("status") or "active"),
            metadata=dict(user.get("metadata") or {}),
            password_hash=str(user.get("password_hash") or ""),
            password_salt=str(user.get("password_salt") or ""),
            email_verified=bool(user.get("email_verified", False)),
            last_login_at=datetime.now(timezone.utc),
        )
        return await self.get_user_account(user_id)

    async def change_user_password(self, *, user_id: str, current_password: str, new_password: str) -> dict | None:
        user = await self._fetch_one_or_none("SELECT * FROM app_users WHERE id = :id", {"id": user_id})
        if user is None:
            return None
        if not verify_password(current_password, str(user.get("password_salt") or ""), str(user.get("password_hash") or "")):
            raise ValueError("INVALID_CURRENT_PASSWORD")
        password_hash, password_salt = hash_password(new_password)
        await self.upsert_user(
            user_id,
            name=str(user.get("name") or user_id),
            email=str(user.get("email") or ""),
            roles=await self._get_user_roles(user_id),
            status=str(user.get("status") or "active"),
            metadata=dict(user.get("metadata") or {}),
            password_hash=password_hash,
            password_salt=password_salt,
            email_verified=bool(user.get("email_verified", False)),
            last_login_at=user.get("last_login_at"),
        )
        return await self.get_user_account(user_id)

    async def delete_user(self, user_id: str) -> bool:
        result = await self.db.execute(text("DELETE FROM app_users WHERE id = :id"), {"id": user_id})
        await self.db.commit()
        return bool(result.rowcount)

    async def apply_user_operation(
        self,
        user_id: str,
        *,
        action: str,
        actor_user_id: str = "",
        credits_delta: int = 0,
        note: str = "",
    ) -> dict | None:
        user = await self._fetch_one_or_none("SELECT * FROM app_users WHERE id = :id", {"id": user_id})
        if user is None:
            return None

        metadata = dict(user.get("metadata") or {})
        now_iso = datetime.now(timezone.utc).isoformat()

        if action == "ban":
            metadata["ban_note"] = note
            metadata["ban_updated_at"] = now_iso
            await self.upsert_user(
                user_id,
                name=str(user.get("name") or user_id),
                email=str(user.get("email") or ""),
                roles=await self._get_user_roles(user_id),
                status="disabled",
                metadata=metadata,
            )
        elif action == "unban":
            metadata["ban_note"] = ""
            metadata["ban_updated_at"] = now_iso
            await self.upsert_user(
                user_id,
                name=str(user.get("name") or user_id),
                email=str(user.get("email") or ""),
                roles=await self._get_user_roles(user_id),
                status="active",
                metadata=metadata,
            )
        elif action == "whitelist":
            metadata["whitelisted"] = True
            metadata["whitelist_note"] = note
            metadata["whitelist_updated_at"] = now_iso
            await self.upsert_user(
                user_id,
                name=str(user.get("name") or user_id),
                email=str(user.get("email") or ""),
                roles=await self._get_user_roles(user_id),
                status=str(user.get("status") or "active"),
                metadata=metadata,
            )
        elif action == "remove_whitelist":
            metadata["whitelisted"] = False
            metadata["whitelist_note"] = note
            metadata["whitelist_updated_at"] = now_iso
            await self.upsert_user(
                user_id,
                name=str(user.get("name") or user_id),
                email=str(user.get("email") or ""),
                roles=await self._get_user_roles(user_id),
                status=str(user.get("status") or "active"),
                metadata=metadata,
            )
        elif action == "grant_credits":
            await self.grant_user_credits(
                user_id,
                credits_delta,
                operator_user_id=actor_user_id,
                source_type="manual_ops",
                source_id=user_id,
                reason=note or "manual_credit_grant",
            )
        else:
            raise ValueError(f"Unsupported user action: {action}")

        await self.create_ops_audit_log(
            actor_user_id=actor_user_id,
            action=action,
            resource_type="user",
            resource_id=user_id,
            target_user_id=user_id,
            detail={"note": note, "credits_delta": credits_delta},
        )
        return await self._fetch_one_or_none("SELECT * FROM app_users WHERE id = :id", {"id": user_id})

    async def _get_user_roles(self, user_id: str) -> list[str]:
        rows = await self._fetch_all(
            "SELECT role_name FROM app_user_roles WHERE user_id = :id ORDER BY role_name ASC",
            {"id": user_id},
        )
        return [str(item.get("role_name") or "") for item in rows if item.get("role_name")]

