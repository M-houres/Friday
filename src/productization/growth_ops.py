"""Growth domain operations."""

from __future__ import annotations

from typing import Any
import uuid

from sqlalchemy import text

from src.productization.base_service import _datetime_from_timestamp, _json_dumps


class GrowthOpsMixin:
    async def create_growth_coupon(
        self,
        *,
        code: str,
        name: str,
        credits_bonus: int = 0,
        status: str = "active",
        max_redemptions: int = 0,
        starts_at: float | int | None = None,
        ends_at: float | int | None = None,
        metadata: dict | None = None,
    ) -> dict:
        coupon_id = uuid.uuid4().hex
        await self.db.execute(
            text(
                """
                INSERT INTO growth_coupons (
                    id, code, name, status, credits_bonus, max_redemptions, redeemed_count,
                    starts_at, ends_at, metadata, created_at, updated_at
                )
                VALUES (
                    :id, :code, :name, :status, :credits_bonus, :max_redemptions, 0,
                    :starts_at, :ends_at, CAST(:metadata AS JSONB), NOW(), NOW()
                )
                ON CONFLICT (code) DO UPDATE
                SET name = EXCLUDED.name,
                    status = EXCLUDED.status,
                    credits_bonus = EXCLUDED.credits_bonus,
                    max_redemptions = EXCLUDED.max_redemptions,
                    starts_at = EXCLUDED.starts_at,
                    ends_at = EXCLUDED.ends_at,
                    metadata = EXCLUDED.metadata,
                    updated_at = NOW()
                """
            ),
            {
                "id": coupon_id,
                "code": code,
                "name": name,
                "status": status,
                "credits_bonus": credits_bonus,
                "max_redemptions": max_redemptions,
                "starts_at": _datetime_from_timestamp(starts_at),
                "ends_at": _datetime_from_timestamp(ends_at),
                "metadata": _json_dumps(metadata or {}),
            },
        )
        await self.db.commit()
        return await self.get_growth_coupon_by_code(code) or {"code": code, "name": name}

    async def list_growth_coupons(self, status: str = "", limit: int = 100) -> list[dict]:
        query = "SELECT * FROM growth_coupons WHERE 1=1"
        params: dict[str, Any] = {"limit": limit}
        if status:
            query += " AND status = :status"
            params["status"] = status
        query += " ORDER BY created_at DESC LIMIT :limit"
        return await self._fetch_all(query, params)

    async def get_growth_coupon(self, coupon_id: str) -> dict | None:
        return await self._fetch_one_or_none("SELECT * FROM growth_coupons WHERE id = :id", {"id": coupon_id})

    async def get_growth_coupon_by_code(self, code: str) -> dict | None:
        return await self._fetch_one_or_none("SELECT * FROM growth_coupons WHERE code = :code", {"code": code})

    async def redeem_growth_coupon(
        self,
        code: str,
        *,
        user_id: str,
        actor_user_id: str = "",
        metadata: dict | None = None,
    ) -> dict:
        coupon = await self.get_growth_coupon_by_code(code)
        if coupon is None:
            raise ValueError("COUPON_NOT_FOUND")
        if str(coupon.get("status") or "") != "active":
            raise ValueError("COUPON_NOT_ACTIVE")
        max_redemptions = int(coupon.get("max_redemptions") or 0)
        redeemed_count = int(coupon.get("redeemed_count") or 0)
        if max_redemptions > 0 and redeemed_count >= max_redemptions:
            raise ValueError("COUPON_EXHAUSTED")
        existing = await self._fetch_one_or_none(
            "SELECT * FROM coupon_redemptions WHERE coupon_id = :coupon_id AND user_id = :user_id",
            {"coupon_id": coupon["id"], "user_id": user_id},
        )
        if existing is not None:
            raise ValueError("COUPON_ALREADY_REDEEMED")

        redemption_id = uuid.uuid4().hex
        credits_bonus = int(coupon.get("credits_bonus") or 0)
        await self.db.execute(
            text(
                """
                INSERT INTO coupon_redemptions (
                    id, coupon_id, user_id, credits_granted, status, metadata, created_at
                )
                VALUES (
                    :id, :coupon_id, :user_id, :credits_granted, 'applied', CAST(:metadata AS JSONB), NOW()
                )
                """
            ),
            {
                "id": redemption_id,
                "coupon_id": coupon["id"],
                "user_id": user_id,
                "credits_granted": credits_bonus,
                "metadata": _json_dumps(metadata or {}),
            },
        )
        await self.db.execute(
            text(
                """
                UPDATE growth_coupons
                SET redeemed_count = redeemed_count + 1,
                    updated_at = NOW()
                WHERE id = :id
                """
            ),
            {"id": coupon["id"]},
        )
        await self.db.commit()
        if credits_bonus > 0:
            await self.grant_user_credits(
                user_id,
                credits_bonus,
                source_type="coupon",
                source_id=str(coupon["id"]),
                operator_user_id=actor_user_id,
                reason=f"coupon:{code}",
            )
        await self.create_ops_audit_log(
            actor_user_id=actor_user_id,
            action="redeem_coupon",
            resource_type="growth_coupon",
            resource_id=str(coupon["id"]),
            target_user_id=user_id,
            detail={"code": code, "credits_bonus": credits_bonus},
        )
        return {"coupon_id": coupon["id"], "code": code, "user_id": user_id, "credits_bonus": credits_bonus}

    async def create_trial_grant(
        self,
        *,
        user_id: str,
        credits_amount: int,
        actor_user_id: str = "",
        reason: str = "",
        metadata: dict | None = None,
    ) -> dict:
        grant_id = uuid.uuid4().hex
        await self.db.execute(
            text(
                """
                INSERT INTO trial_grants (
                    id, user_id, grant_type, credits_amount, status,
                    operator_user_id, reason, metadata, created_at, applied_at
                )
                VALUES (
                    :id, :user_id, 'credits', :credits_amount, 'active',
                    :operator_user_id, :reason, CAST(:metadata AS JSONB), NOW(), NOW()
                )
                """
            ),
            {
                "id": grant_id,
                "user_id": user_id,
                "credits_amount": credits_amount,
                "operator_user_id": actor_user_id,
                "reason": reason,
                "metadata": _json_dumps(metadata or {}),
            },
        )
        await self.db.commit()
        await self.grant_user_credits(
            user_id,
            credits_amount,
            source_type="trial_grant",
            source_id=grant_id,
            operator_user_id=actor_user_id,
            reason=reason or "trial_grant",
        )
        await self.create_ops_audit_log(
            actor_user_id=actor_user_id,
            action="create_trial_grant",
            resource_type="trial_grant",
            resource_id=grant_id,
            target_user_id=user_id,
            detail={"credits_amount": credits_amount, "reason": reason},
        )
        return await self._fetch_one_or_none("SELECT * FROM trial_grants WHERE id = :id", {"id": grant_id}) or {"id": grant_id}

    async def list_trial_grants(self, user_id: str = "", limit: int = 100) -> list[dict]:
        query = "SELECT * FROM trial_grants WHERE 1=1"
        params: dict[str, Any] = {"limit": limit}
        if user_id:
            query += " AND user_id = :user_id"
            params["user_id"] = user_id
        query += " ORDER BY created_at DESC LIMIT :limit"
        return await self._fetch_all(query, params)

