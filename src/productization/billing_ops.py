"""Billing and entitlement domain operations."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import uuid

from sqlalchemy import text

from src.productization.base_service import _datetime_from_timestamp, _json_dumps


class BillingOpsMixin:
    async def record_payment_callback(
        self,
        *,
        provider: str,
        provider_event_id: str = "",
        provider_order_id: str = "",
        order_id: str = "",
        payload: dict | None = None,
        status: str = "received",
    ) -> dict:
        callback_id = uuid.uuid4().hex
        await self.db.execute(
            text(
                """
                INSERT INTO payment_callback_events (
                    id, provider, provider_event_id, provider_order_id, order_id,
                    status, payload, created_at
                )
                VALUES (
                    :id, :provider, :provider_event_id, :provider_order_id, :order_id,
                    :status, CAST(:payload AS JSONB), NOW()
                )
                """
            ),
            {
                "id": callback_id,
                "provider": provider,
                "provider_event_id": provider_event_id,
                "provider_order_id": provider_order_id,
                "order_id": order_id,
                "status": status,
                "payload": _json_dumps(payload or {}),
            },
        )
        await self.db.commit()
        return await self.get_payment_callback_event(callback_id) or {"id": callback_id}

    async def get_payment_callback_event(self, callback_id: str) -> dict | None:
        return await self._fetch_one_or_none(
            "SELECT * FROM payment_callback_events WHERE id = :id",
            {"id": callback_id},
        )

    async def list_payment_callback_events(self, provider: str = "", limit: int = 100) -> list[dict]:
        query = "SELECT * FROM payment_callback_events WHERE 1=1"
        params: dict[str, Any] = {"limit": limit}
        if provider:
            query += " AND provider = :provider"
            params["provider"] = provider
        query += " ORDER BY created_at DESC LIMIT :limit"
        return await self._fetch_all(query, params)

    async def process_payment_callback(
        self,
        *,
        provider: str,
        provider_order_id: str = "",
        order_id: str = "",
        provider_event_id: str = "",
        payment_status: str = "paid",
        amount_cents: int = 0,
        payload: dict | None = None,
        actor_user_id: str = "",
    ) -> dict:
        event = await self.record_payment_callback(
            provider=provider,
            provider_event_id=provider_event_id,
            provider_order_id=provider_order_id,
            order_id=order_id,
            payload={**(payload or {}), "payment_status": payment_status, "amount_cents": amount_cents},
            status="received",
        )
        order = None
        if order_id:
            order = await self.get_payment_order(order_id)
        if order is None and provider_order_id:
            order = await self._fetch_one_or_none(
                "SELECT * FROM payment_orders WHERE provider_order_id = :provider_order_id",
                {"provider_order_id": provider_order_id},
            )
        if order is None:
            raise ValueError("ORDER_NOT_FOUND")

        if payment_status == "paid":
            updated = await self.create_payment_order(
                str(order.get("user_id") or ""),
                order_id=str(order.get("id") or ""),
                plan_id=str(order.get("plan_id") or ""),
                order_type=str(order.get("order_type") or "subscription"),
                amount_cents=int(amount_cents or order.get("amount_cents") or 0),
                currency=str(order.get("currency") or "CNY"),
                status="paid",
                provider=provider or str(order.get("provider") or ""),
                provider_order_id=provider_order_id or str(order.get("provider_order_id") or ""),
                credits_delta=int(order.get("credits_delta") or 0),
                detail=dict(order.get("detail") or {}),
                paid_at=datetime.now(timezone.utc).timestamp(),
            )
            await self.db.execute(
                text(
                    """
                    UPDATE payment_callback_events
                    SET status = 'processed',
                        processed_at = NOW()
                    WHERE id = :id
                    """
                ),
                {"id": event["id"]},
            )
            await self.db.commit()
            await self.create_ops_audit_log(
                actor_user_id=actor_user_id,
                action="process_payment_callback",
                resource_type="payment_callback",
                resource_id=str(event["id"]),
                target_user_id=str(order.get("user_id") or ""),
                detail={"order_id": order.get("id"), "provider_order_id": provider_order_id, "payment_status": payment_status},
            )
            return {"event": await self.get_payment_callback_event(str(event["id"])), "order": updated}

        raise ValueError("UNSUPPORTED_PAYMENT_STATUS")


    async def create_entitlement_ledger_entry(
        self,
        user_id: str,
        *,
        change_type: str,
        delta_credits: int,
        balance_after: int,
        source_type: str = "",
        source_id: str = "",
        operator_user_id: str = "",
        reason: str = "",
        metadata: dict | None = None,
    ) -> dict:
        entry_id = uuid.uuid4().hex
        await self.db.execute(
            text(
                """
                INSERT INTO entitlement_ledger (
                    id, user_id, change_type, delta_credits, balance_after,
                    source_type, source_id, operator_user_id, reason, metadata, created_at
                )
                VALUES (
                    :id, :user_id, :change_type, :delta_credits, :balance_after,
                    :source_type, :source_id, :operator_user_id, :reason, CAST(:metadata AS JSONB), NOW()
                )
                """
            ),
            {
                "id": entry_id,
                "user_id": user_id,
                "change_type": change_type,
                "delta_credits": delta_credits,
                "balance_after": balance_after,
                "source_type": source_type,
                "source_id": source_id,
                "operator_user_id": operator_user_id,
                "reason": reason,
                "metadata": _json_dumps(metadata or {}),
            },
        )
        await self.db.commit()
        return {"ledger_id": entry_id, "user_id": user_id, "change_type": change_type}

    async def list_entitlement_ledger(
        self,
        *,
        user_id: str = "",
        source_type: str = "",
        source_id: str = "",
        limit: int = 100,
    ) -> list[dict]:
        query = "SELECT * FROM entitlement_ledger WHERE 1=1"
        params: dict[str, Any] = {"limit": limit}
        if user_id:
            query += " AND user_id = :user_id"
            params["user_id"] = user_id
        if source_type:
            query += " AND source_type = :source_type"
            params["source_type"] = source_type
        if source_id:
            query += " AND source_id = :source_id"
            params["source_id"] = source_id
        query += " ORDER BY created_at DESC LIMIT :limit"
        return await self._fetch_all(query, params)

    async def create_billing_plan(
        self,
        name: str,
        *,
        plan_id: str = "",
        plan_type: str = "subscription",
        price_cents: int = 0,
        currency: str = "CNY",
        interval: str = "month",
        credits: int = 0,
        features: list[str] | None = None,
        status: str = "active",
        metadata: dict | None = None,
    ) -> dict:
        plan_id = plan_id or uuid.uuid4().hex
        await self.db.execute(
            text(
                """
                INSERT INTO billing_plans (
                    id, name, plan_type, price_cents, currency, interval, credits,
                    features, status, metadata, created_at, updated_at
                )
                VALUES (
                    :id, :name, :plan_type, :price_cents, :currency, :interval, :credits,
                    CAST(:features AS JSONB), :status, CAST(:metadata AS JSONB), NOW(), NOW()
                )
                ON CONFLICT (id) DO UPDATE
                SET name = EXCLUDED.name,
                    plan_type = EXCLUDED.plan_type,
                    price_cents = EXCLUDED.price_cents,
                    currency = EXCLUDED.currency,
                    interval = EXCLUDED.interval,
                    credits = EXCLUDED.credits,
                    features = EXCLUDED.features,
                    status = EXCLUDED.status,
                    metadata = EXCLUDED.metadata,
                    updated_at = NOW()
                """
            ),
            {
                "id": plan_id,
                "name": name,
                "plan_type": plan_type,
                "price_cents": price_cents,
                "currency": currency,
                "interval": interval,
                "credits": credits,
                "features": _json_dumps(features or []),
                "status": status,
                "metadata": _json_dumps(metadata or {}),
            },
        )
        await self.db.commit()
        return {
            "plan_id": plan_id,
            "name": name,
            "plan_type": plan_type,
            "price_cents": price_cents,
            "credits": credits,
            "status": status,
        }

    async def resolve_workflow_charge(
        self,
        *,
        project_id: str = "",
        page_id: str = "",
        context: dict | None = None,
    ) -> dict:
        payload = dict(context or {})
        billing = payload.get("_billing") if isinstance(payload.get("_billing"), dict) else {}
        if not billing and project_id and page_id:
            try:
                from src.projects.registry import project_registry

                page = project_registry.get_page(project_id, page_id)
                if page is not None:
                    page_billing = page.get("billing")
                    if isinstance(page_billing, dict):
                        billing = page_billing
            except Exception:
                billing = {}

        required = bool(billing.get("required"))
        credits_cost = int(billing.get("credits_cost") or 0)
        reason = str(billing.get("reason") or "")
        if credits_cost <= 0:
            required = False

        return {
            "required": required,
            "credits_cost": max(credits_cost, 0),
            "reason": reason or "chargeable_workflow",
        }

    async def list_billing_plans(self, status: str = "", plan_type: str = "") -> list[dict]:
        query = "SELECT * FROM billing_plans WHERE 1=1"
        params: dict[str, Any] = {}
        if status:
            query += " AND status = :status"
            params["status"] = status
        if plan_type:
            query += " AND plan_type = :plan_type"
            params["plan_type"] = plan_type
        query += " ORDER BY price_cents ASC, created_at DESC"
        return await self._fetch_all(query, params)

    async def get_billing_plan(self, plan_id: str) -> dict | None:
        return await self._fetch_one_or_none(
            "SELECT * FROM billing_plans WHERE id = :id",
            {"id": plan_id},
        )

    async def delete_billing_plan(self, plan_id: str) -> bool:
        result = await self.db.execute(text("DELETE FROM billing_plans WHERE id = :id"), {"id": plan_id})
        await self.db.commit()
        return bool(result.rowcount)

    async def upsert_user_entitlement(
        self,
        user_id: str,
        *,
        active_plan_id: str = "",
        subscription_status: str = "inactive",
        credits_balance: int = 0,
        credits_granted_total: int = 0,
        credits_used_total: int = 0,
        expires_at: float | int | None = None,
        metadata: dict | None = None,
    ) -> dict:
        await self.db.execute(
            text(
                """
                INSERT INTO user_entitlements (
                    user_id, active_plan_id, subscription_status, credits_balance,
                    credits_granted_total, credits_used_total, expires_at, metadata, created_at, updated_at
                )
                VALUES (
                    :user_id, NULLIF(:active_plan_id, ''), :subscription_status, :credits_balance,
                    :credits_granted_total, :credits_used_total, :expires_at, CAST(:metadata AS JSONB), NOW(), NOW()
                )
                ON CONFLICT (user_id) DO UPDATE
                SET active_plan_id = NULLIF(EXCLUDED.active_plan_id, ''),
                    subscription_status = EXCLUDED.subscription_status,
                    credits_balance = EXCLUDED.credits_balance,
                    credits_granted_total = EXCLUDED.credits_granted_total,
                    credits_used_total = EXCLUDED.credits_used_total,
                    expires_at = EXCLUDED.expires_at,
                    metadata = EXCLUDED.metadata,
                    updated_at = NOW()
                """
            ),
            {
                "user_id": user_id,
                "active_plan_id": active_plan_id,
                "subscription_status": subscription_status,
                "credits_balance": credits_balance,
                "credits_granted_total": credits_granted_total,
                "credits_used_total": credits_used_total,
                "expires_at": _datetime_from_timestamp(expires_at),
                "metadata": _json_dumps(metadata or {}),
            },
        )
        await self.db.commit()
        return await self.get_user_entitlement(user_id) or {"user_id": user_id}

    async def get_user_entitlement(self, user_id: str) -> dict | None:
        return await self._fetch_one_or_none(
            """
            SELECT e.*, p.name AS active_plan_name
            FROM user_entitlements e
            LEFT JOIN billing_plans p ON p.id = e.active_plan_id
            WHERE e.user_id = :user_id
            """,
            {"user_id": user_id},
        )

    async def list_user_entitlements(self, subscription_status: str = "", limit: int = 100) -> list[dict]:
        query = """
            SELECT e.*, p.name AS active_plan_name
            FROM user_entitlements e
            LEFT JOIN billing_plans p ON p.id = e.active_plan_id
            WHERE 1=1
        """
        params: dict[str, Any] = {"limit": limit}
        if subscription_status:
            query += " AND e.subscription_status = :subscription_status"
            params["subscription_status"] = subscription_status
        query += " ORDER BY e.updated_at DESC LIMIT :limit"
        return await self._fetch_all(query, params)

    async def ensure_user_entitlement(self, user_id: str) -> dict:
        entitlement = await self.get_user_entitlement(user_id)
        if entitlement is not None:
            return entitlement
        await self.upsert_user(
            user_id,
            name=user_id,
            roles=["user"],
            metadata={"source": "auto_created_by_billing"},
        )
        return await self.upsert_user_entitlement(user_id)

    async def grant_user_credits(
        self,
        user_id: str,
        credits: int,
        *,
        active_plan_id: str = "",
        subscription_status: str | None = None,
        metadata_patch: dict | None = None,
        source_type: str = "",
        source_id: str = "",
        operator_user_id: str = "",
        reason: str = "",
    ) -> dict:
        entitlement = await self.ensure_user_entitlement(user_id)
        next_status = subscription_status if subscription_status is not None else str(entitlement.get("subscription_status") or "inactive")
        next_metadata = dict(entitlement.get("metadata") or {})
        if metadata_patch:
            next_metadata.update(metadata_patch)
        updated = await self.upsert_user_entitlement(
            user_id,
            active_plan_id=active_plan_id or str(entitlement.get("active_plan_id") or ""),
            subscription_status=next_status,
            credits_balance=int(entitlement.get("credits_balance") or 0) + int(credits),
            credits_granted_total=int(entitlement.get("credits_granted_total") or 0) + int(credits),
            credits_used_total=int(entitlement.get("credits_used_total") or 0),
            expires_at=entitlement.get("expires_at").timestamp() if getattr(entitlement.get("expires_at"), "timestamp", None) else None,
            metadata=next_metadata,
        )
        await self.create_entitlement_ledger_entry(
            user_id,
            change_type="grant",
            delta_credits=int(credits),
            balance_after=int(updated.get("credits_balance") or 0),
            source_type=source_type,
            source_id=source_id,
            operator_user_id=operator_user_id,
            reason=reason,
            metadata={"subscription_status": next_status},
        )
        return updated

    async def consume_user_credits(
        self,
        user_id: str,
        credits: int,
        *,
        reason: str = "",
        allow_zero: bool = False,
        source_type: str = "",
        source_id: str = "",
        operator_user_id: str = "",
    ) -> dict:
        amount = int(credits or 0)
        if amount < 0:
            raise ValueError("credits must be >= 0")
        entitlement = await self.ensure_user_entitlement(user_id)
        if amount == 0 and allow_zero:
            return entitlement
        balance = int(entitlement.get("credits_balance") or 0)
        if amount > balance:
            raise ValueError("INSUFFICIENT_CREDITS")
        next_metadata = dict(entitlement.get("metadata") or {})
        if reason:
            next_metadata["last_consumption_reason"] = reason
        updated = await self.upsert_user_entitlement(
            user_id,
            active_plan_id=str(entitlement.get("active_plan_id") or ""),
            subscription_status=str(entitlement.get("subscription_status") or "inactive"),
            credits_balance=balance - amount,
            credits_granted_total=int(entitlement.get("credits_granted_total") or 0),
            credits_used_total=int(entitlement.get("credits_used_total") or 0) + amount,
            expires_at=entitlement.get("expires_at").timestamp() if getattr(entitlement.get("expires_at"), "timestamp", None) else None,
            metadata=next_metadata,
        )
        await self.create_entitlement_ledger_entry(
            user_id,
            change_type="consume",
            delta_credits=-amount,
            balance_after=int(updated.get("credits_balance") or 0),
            source_type=source_type,
            source_id=source_id,
            operator_user_id=operator_user_id,
            reason=reason,
        )
        return updated

    async def refund_user_credits(
        self,
        user_id: str,
        credits: int,
        *,
        reason: str = "",
        source_type: str = "",
        source_id: str = "",
        operator_user_id: str = "",
    ) -> dict:
        amount = int(credits or 0)
        if amount < 0:
            raise ValueError("credits must be >= 0")
        entitlement = await self.ensure_user_entitlement(user_id)
        next_metadata = dict(entitlement.get("metadata") or {})
        if reason:
            next_metadata["last_refund_reason"] = reason
        used_total = int(entitlement.get("credits_used_total") or 0)
        updated = await self.upsert_user_entitlement(
            user_id,
            active_plan_id=str(entitlement.get("active_plan_id") or ""),
            subscription_status=str(entitlement.get("subscription_status") or "inactive"),
            credits_balance=int(entitlement.get("credits_balance") or 0) + amount,
            credits_granted_total=int(entitlement.get("credits_granted_total") or 0),
            credits_used_total=max(used_total - amount, 0),
            expires_at=entitlement.get("expires_at").timestamp() if getattr(entitlement.get("expires_at"), "timestamp", None) else None,
            metadata=next_metadata,
        )
        await self.create_entitlement_ledger_entry(
            user_id,
            change_type="refund",
            delta_credits=amount,
            balance_after=int(updated.get("credits_balance") or 0),
            source_type=source_type,
            source_id=source_id,
            operator_user_id=operator_user_id,
            reason=reason,
        )
        return updated
    
    async def preview_user_charge(
        self,
        user_id: str,
        *,
        project_id: str = "",
        page_id: str = "",
        context: dict | None = None,
    ) -> dict:
        charge = await self.resolve_workflow_charge(project_id=project_id, page_id=page_id, context=context)
        entitlement = (
            await self.ensure_user_entitlement(user_id)
            if charge["required"]
            else await self.get_user_entitlement(user_id)
        ) or {"credits_balance": 0}
        balance = int(entitlement.get("credits_balance") or 0)
        cost = int(charge.get("credits_cost") or 0)
        return {
            **charge,
            "credits_balance": balance,
            "can_run": (not charge["required"]) or balance >= cost,
        }

    async def create_payment_order(
        self,
        user_id: str,
        *,
        order_id: str = "",
        plan_id: str = "",
        order_type: str = "subscription",
        amount_cents: int = 0,
        currency: str = "CNY",
        status: str = "pending",
        provider: str = "",
        provider_order_id: str = "",
        credits_delta: int = 0,
        detail: dict | None = None,
        paid_at: float | int | None = None,
    ) -> dict:
        order_id = order_id or uuid.uuid4().hex
        existing_order = await self.get_payment_order(order_id)
        await self.db.execute(
            text(
                """
                INSERT INTO payment_orders (
                    id, user_id, plan_id, order_type, amount_cents, currency, status,
                    provider, provider_order_id, credits_delta, detail, benefits_applied_at, paid_at, created_at, updated_at
                )
                VALUES (
                    :id, :user_id, NULLIF(:plan_id, ''), :order_type, :amount_cents, :currency, :status,
                    :provider, :provider_order_id, :credits_delta, CAST(:detail AS JSONB), :benefits_applied_at, :paid_at, NOW(), NOW()
                )
                ON CONFLICT (id) DO UPDATE
                SET user_id = EXCLUDED.user_id,
                    plan_id = NULLIF(EXCLUDED.plan_id, ''),
                    order_type = EXCLUDED.order_type,
                    amount_cents = EXCLUDED.amount_cents,
                    currency = EXCLUDED.currency,
                    status = EXCLUDED.status,
                    provider = EXCLUDED.provider,
                    provider_order_id = EXCLUDED.provider_order_id,
                    credits_delta = EXCLUDED.credits_delta,
                    detail = EXCLUDED.detail,
                    benefits_applied_at = COALESCE(payment_orders.benefits_applied_at, EXCLUDED.benefits_applied_at),
                    paid_at = EXCLUDED.paid_at,
                    updated_at = NOW()
                """
            ),
            {
                "id": order_id,
                "user_id": user_id,
                "plan_id": plan_id,
                "order_type": order_type,
                "amount_cents": amount_cents,
                "currency": currency,
                "status": status,
                "provider": provider,
                "provider_order_id": provider_order_id,
                "credits_delta": credits_delta,
                "detail": _json_dumps(detail or {}),
                "benefits_applied_at": None,
                "paid_at": _datetime_from_timestamp(paid_at),
            },
        )
        current_order = await self.get_payment_order(order_id)
        already_applied = bool(existing_order and existing_order.get("benefits_applied_at"))
        if status == "paid" and not already_applied and current_order is not None:
            plan = await self.get_billing_plan(plan_id) if plan_id else None
            grant_credits = int(plan.get("credits") or 0) + int(credits_delta or 0) if plan is not None else int(credits_delta or 0)
            if grant_credits > 0:
                await self.grant_user_credits(
                    user_id,
                    grant_credits,
                    active_plan_id=plan_id,
                    subscription_status="active" if order_type == "subscription" else None,
                    metadata_patch={"last_paid_order_id": order_id},
                    source_type="payment_order",
                    source_id=order_id,
                    reason="payment_paid",
                )
            elif order_type == "subscription" and plan_id:
                entitlement = await self.ensure_user_entitlement(user_id)
                await self.upsert_user_entitlement(
                    user_id,
                    active_plan_id=plan_id,
                    subscription_status="active",
                    credits_balance=int(entitlement.get("credits_balance") or 0),
                    credits_granted_total=int(entitlement.get("credits_granted_total") or 0),
                    credits_used_total=int(entitlement.get("credits_used_total") or 0),
                    expires_at=entitlement.get("expires_at").timestamp() if getattr(entitlement.get("expires_at"), "timestamp", None) else None,
                    metadata=dict(entitlement.get("metadata") or {}),
                )
            await self.db.execute(
                text(
                    """
                    UPDATE payment_orders
                    SET benefits_applied_at = COALESCE(benefits_applied_at, NOW())
                    WHERE id = :id
                    """
                ),
                {"id": order_id},
            )
        await self.db.commit()
        return await self.get_payment_order(order_id) or {"order_id": order_id, "status": status}

    async def refund_payment_order(
        self,
        order_id: str,
        *,
        actor_user_id: str = "",
        reason: str = "",
    ) -> dict | None:
        order = await self.get_payment_order(order_id)
        if order is None:
            return None
        if str(order.get("status") or "") == "refunded":
            return order
        refund_credits = int(order.get("credits_delta") or 0)
        if refund_credits > 0 and str(order.get("benefits_applied_at") or ""):
            await self.consume_user_credits(
                str(order.get("user_id") or ""),
                refund_credits,
                reason=reason or "order_refund",
                source_type="payment_order_refund",
                source_id=order_id,
                operator_user_id=actor_user_id,
            )
        await self.db.execute(
            text(
                """
                UPDATE payment_orders
                SET status = 'refunded',
                    updated_at = NOW()
                WHERE id = :id
                """
            ),
            {"id": order_id},
        )
        await self.db.commit()
        await self.create_ops_audit_log(
            actor_user_id=actor_user_id,
            action="refund_payment_order",
            resource_type="payment_order",
            resource_id=order_id,
            target_user_id=str(order.get("user_id") or ""),
            detail={"reason": reason, "credits_delta": refund_credits},
        )
        return await self.get_payment_order(order_id)

    async def get_payment_order(self, order_id: str) -> dict | None:
        return await self._fetch_one_or_none(
            """
            SELECT o.*, p.name AS plan_name
            FROM payment_orders o
            LEFT JOIN billing_plans p ON p.id = o.plan_id
            WHERE o.id = :id
            """,
            {"id": order_id},
        )

    async def list_payment_orders(
        self,
        *,
        user_id: str = "",
        status: str = "",
        order_type: str = "",
        limit: int = 100,
    ) -> list[dict]:
        query = """
            SELECT o.*, p.name AS plan_name
            FROM payment_orders o
            LEFT JOIN billing_plans p ON p.id = o.plan_id
            WHERE 1=1
        """
        params: dict[str, Any] = {"limit": limit}
        if user_id:
            query += " AND o.user_id = :user_id"
            params["user_id"] = user_id
        if status:
            query += " AND o.status = :status"
            params["status"] = status
        if order_type:
            query += " AND o.order_type = :order_type"
            params["order_type"] = order_type
        query += " ORDER BY o.created_at DESC LIMIT :limit"
        return await self._fetch_all(query, params)

