import asyncio
from unittest.mock import AsyncMock


def test_normalize_result_payload_collects_downloads():
    from src.productization.result_protocol import normalize_result_payload

    normalized = normalize_result_payload(
        {
            "summary": "done",
            "download_url": "/a",
            "filename": "a.txt",
            "downloads": [{"filename": "b.txt", "download_url": "/b"}],
            "structured_result": {"ok": True},
        },
        source="skill:test",
    )

    assert normalized["status"] == "completed"
    assert normalized["source"] == "skill:test"
    assert len(normalized["downloads"]) == 2
    assert normalized["structured_result"]["ok"] is True


def test_async_job_manager_runs_executor():
    from src.productization.async_jobs import AsyncJobManager

    manager = AsyncJobManager()

    async def run():
        async def executor(job_id: str, payload: dict) -> dict:
            return {"job_id": job_id, "echo": payload["value"]}

        manager.configure(executor)
        await manager.start()
        job = await manager.enqueue("test", {"value": "ok"}, priority=1)
        for _ in range(20):
            current = manager.get(job["job_id"])
            if current and current["status"] in {"completed", "failed"}:
                break
            await asyncio.sleep(0.02)
        done = manager.get(job["job_id"])
        await manager.stop()
        return done

    result = asyncio.run(run())
    assert result["status"] == "completed"
    assert result["result"]["echo"] == "ok"


def test_async_job_manager_cancel_is_persisted_in_memory():
    from src.productization.async_jobs import AsyncJobManager

    manager = AsyncJobManager()

    async def run():
        job = await manager.enqueue("test", {"value": "ok"}, priority=2)
        cancelled = await manager.cancel(job["job_id"])
        return cancelled, manager.get(job["job_id"])

    cancelled, result = asyncio.run(run())
    assert cancelled is True
    assert result["status"] == "cancelled"
    assert result["error"] == ""
    assert result["completed_at"] is not None


def test_async_job_manager_normalizes_persisted_jobs():
    from datetime import datetime, timezone

    from src.productization.async_jobs import AsyncJobManager

    job = AsyncJobManager._normalize_persisted_job(
        {
            "id": "job-1",
            "job_type": "workflow",
            "status": "queued",
            "priority": 3,
            "payload": {"user_id": "u1"},
            "created_at": datetime(2026, 5, 14, 0, 0, tzinfo=timezone.utc),
            "started_at": None,
            "completed_at": None,
        }
    )

    assert job["job_id"] == "job-1"
    assert job["priority"] == 3
    assert job["payload"]["user_id"] == "u1"
    assert isinstance(job["created_at"], float)


def test_product_ops_template_render_and_knowledge_rank():
    from src.productization.service import ProductOpsService

    rendered = ProductOpsService.render_template_content(
        "hello {{name}} from {{team}}",
        {"name": "Friday", "team": "AI"},
    )
    assert rendered == "hello Friday from AI"

    ranked = ProductOpsService.rank_knowledge_documents(
        [
            {"title": "合同法风险", "content": "付款条款和违约责任"},
            {"title": "销售方案", "content": "客户背景和价值主张"},
        ],
        query="合同 风险",
        limit=2,
    )
    assert ranked[0]["title"] == "合同法风险"


def test_render_template_handles_non_string_values():
    from src.productization.service import ProductOpsService

    rendered = ProductOpsService.render_template_content(
        "count={{count}}, ready={{ready}}",
        {"count": 3, "ready": True},
    )

    assert rendered == "count=3, ready=True"


def test_rank_knowledge_documents_includes_score_and_snippet():
    from src.productization.service import ProductOpsService

    ranked = ProductOpsService.rank_knowledge_documents(
        [
            {"title": "合同付款规范", "content": "付款条款需写清回款时间和违约责任", "tags": ["legal", "payment"]},
            {"title": "市场方案", "content": "渠道策略和品牌传播", "tags": ["marketing"]},
        ],
        query="付款 违约",
        limit=2,
    )

    assert ranked[0]["title"] == "合同付款规范"
    assert ranked[0]["score"] > 0
    assert "付款" in ranked[0]["snippet"] or "违约" in ranked[0]["snippet"]


def test_async_job_manager_database_cancel_requires_queued_status():
    from src.productization.async_jobs import AsyncJobManager

    class FakeStore:
        async def get(self, job_id: str):
            return {"id": job_id, "status": "running"}

        async def update(self, job_id: str, **changes):
            return None

    manager = AsyncJobManager()
    manager._store = FakeStore()

    async def run():
        return await manager.cancel("job-running")

    assert asyncio.run(run()) is False


def test_async_job_manager_starts_heartbeat_for_store():
    from src.productization.async_jobs import AsyncJobManager

    async def run():
        manager = AsyncJobManager()
        manager._running = True
        manager._store = object()
        task = manager._start_heartbeat("job-1")
        assert task is not None
        assert "job-1" in manager._heartbeat_tasks
        task.cancel()

    asyncio.run(run())


def test_product_ops_update_async_job_without_changes_reads_current_job():
    from src.productization.service import ProductOpsService

    class FakeResult:
        def __init__(self, row):
            self._row = row

        def fetchone(self):
            return self._row

    class FakeRow:
        def __init__(self, mapping):
            self._mapping = mapping

    class FakeSession:
        def __init__(self):
            self.execute = AsyncMock(return_value=FakeResult(FakeRow({"id": "job-1", "status": "queued"})))
            self.commit = AsyncMock()

    async def run():
        session = FakeSession()
        service = ProductOpsService(session)
        job = await service.update_async_job("job-1")
        return session, job

    session, job = asyncio.run(run())
    session.commit.assert_not_awaited()
    assert session.execute.await_count == 1
    assert job["id"] == "job-1"
    assert job["status"] == "queued"


def test_product_ops_missing_table_reads_return_empty_or_none():
    from sqlalchemy.exc import ProgrammingError

    from src.productization.service import ProductOpsService

    class MissingTableSession:
        async def execute(self, *args, **kwargs):
            raise ProgrammingError("SELECT 1", {}, Exception('relation "approval_requests" does not exist'))

    async def run():
        service = ProductOpsService(MissingTableSession())
        users = await service.list_users()
        approvals = await service.list_approval_requests()
        approval = await service.get_approval_request("a1")
        job = await service.get_async_job("j1")
        return users, approvals, approval, job

    users, approvals, approval, job = asyncio.run(run())
    assert users == []
    assert approvals == []
    assert approval is None
    assert job is None


def test_product_ops_billing_helpers_query_builder():
    from src.productization.service import ProductOpsService

    class FakeResult:
        def __init__(self, rows):
            self._rows = rows

        def fetchall(self):
            return self._rows

        def fetchone(self):
            return self._rows[0] if self._rows else None

    class FakeRow:
        def __init__(self, mapping):
            self._mapping = mapping

    class FakeSession:
        def __init__(self):
            self.execute = AsyncMock(side_effect=[
                FakeResult([FakeRow({"id": "plan-1", "name": "月度会员", "status": "active"})]),
                FakeResult([FakeRow({"id": "order-1", "user_id": "u1", "status": "paid"})]),
                FakeResult([FakeRow({"user_id": "u1", "subscription_status": "active"})]),
            ])

    async def run():
        service = ProductOpsService(FakeSession())
        plans = await service.list_billing_plans(status="active", plan_type="subscription")
        orders = await service.list_payment_orders(user_id="u1", status="paid", order_type="subscription", limit=10)
        entitlements = await service.list_user_entitlements(subscription_status="active", limit=10)
        return plans, orders, entitlements

    plans, orders, entitlements = asyncio.run(run())
    assert plans[0]["name"] == "月度会员"
    assert orders[0]["user_id"] == "u1"
    assert entitlements[0]["subscription_status"] == "active"


def test_resolve_workflow_charge_from_context():
    from src.productization.service import ProductOpsService

    service = ProductOpsService(object())
    result = asyncio.run(
        service.resolve_workflow_charge(
            context={"_billing": {"required": True, "credits_cost": 9, "reason": "premium_page"}}
        )
    )

    assert result["required"] is True
    assert result["credits_cost"] == 9
    assert result["reason"] == "premium_page"


def test_preview_user_charge_uses_existing_entitlement():
    from src.productization.service import ProductOpsService

    class StubService(ProductOpsService):
        async def resolve_workflow_charge(self, **kwargs):
            return {"required": True, "credits_cost": 6, "reason": "premium"}

        async def ensure_user_entitlement(self, user_id: str):
            return {"user_id": user_id, "credits_balance": 8}

    result = asyncio.run(StubService(object()).preview_user_charge("u1"))
    assert result["can_run"] is True
    assert result["credits_balance"] == 8


def test_preview_user_charge_blocks_when_balance_is_insufficient():
    from src.productization.service import ProductOpsService

    class StubService(ProductOpsService):
        async def resolve_workflow_charge(self, **kwargs):
            return {"required": True, "credits_cost": 12, "reason": "premium"}

        async def ensure_user_entitlement(self, user_id: str):
            return {"user_id": user_id, "credits_balance": 3}

    result = asyncio.run(StubService(object()).preview_user_charge("u1"))
    assert result["can_run"] is False
    assert result["credits_balance"] == 3


def test_retry_async_job_creates_new_queued_job_and_audit():
    from src.productization.service import ProductOpsService

    class StubService(ProductOpsService):
        def __init__(self):
            super().__init__(object())
            self.created = None
            self.audit = None

        async def get_async_job(self, job_id: str, user_id: str = ""):
            return {
                "id": job_id,
                "job_type": "workflow",
                "priority": 3,
                "payload": {"task": "redo", "user_id": "u1"},
            }

        async def create_async_job(self, job_id: str, job_type: str, payload: dict, **kwargs):
            self.created = {"job_id": job_id, "job_type": job_type, "payload": payload, **kwargs}
            return {"job_id": job_id, "status": kwargs.get("status", "queued")}

        async def create_ops_audit_log(self, **kwargs):
            self.audit = kwargs
            return {"audit_log_id": "a1"}

    service = StubService()
    result = asyncio.run(service.retry_async_job("job-1", actor_user_id="ops-1"))
    assert result["status"] == "queued"
    assert service.created["job_type"] == "workflow"
    assert service.audit["action"] == "retry_job"


def test_refund_workflow_charge_marks_billing_refunded():
    from src.productization.service import ProductOpsService

    class FakeSession:
        def __init__(self):
            self.execute = AsyncMock()
            self.commit = AsyncMock()

    class StubService(ProductOpsService):
        def __init__(self):
            super().__init__(FakeSession())
            self.refund_called = None
            self.audit = None

        async def get_result_record(self, workflow_id: str, user_id: str = ""):
            return {
                "workflow_id": workflow_id,
                "user_id": "u1",
                "normalized_result": {
                    "summary": "ok",
                    "billing": {"credits_cost": 8, "required": True, "charged": True, "refunded": False},
                },
            }

        async def refund_user_credits(self, user_id: str, credits: int, **kwargs):
            self.refund_called = {"user_id": user_id, "credits": credits, **kwargs}
            return {"user_id": user_id, "credits_balance": 18}

        async def create_ops_audit_log(self, **kwargs):
            self.audit = kwargs
            return {"audit_log_id": "a1"}

    service = StubService()
    result = asyncio.run(service.refund_workflow_charge("wf-1", actor_user_id="ops-1", reason="manual"))
    assert result["credits_refunded"] == 8
    assert result["billing"]["refunded"] is True
    assert service.refund_called["source_type"] == "workflow_refund"
    assert service.audit["action"] == "refund_workflow_charge"


def test_redeem_growth_coupon_grants_credits_and_audits():
    from src.productization.service import ProductOpsService

    class StubService(ProductOpsService):
        def __init__(self):
            self.session = type("Session", (), {"execute": AsyncMock(), "commit": AsyncMock()})()
            super().__init__(self.session)
            self.grant_called = None
            self.audit = None

        async def get_growth_coupon_by_code(self, code: str):
            return {"id": "coupon-1", "code": code, "status": "active", "credits_bonus": 15, "max_redemptions": 100, "redeemed_count": 1}

        async def _fetch_one_or_none(self, query, params=None):
            return None

        async def grant_user_credits(self, user_id: str, credits: int, **kwargs):
            self.grant_called = {"user_id": user_id, "credits": credits, **kwargs}
            return {"user_id": user_id, "credits_balance": 15}

        async def create_ops_audit_log(self, **kwargs):
            self.audit = kwargs
            return {"audit_log_id": "a1"}

    service = StubService()
    result = asyncio.run(service.redeem_growth_coupon("WELCOME15", user_id="u1", actor_user_id="ops-1"))
    assert result["credits_bonus"] == 15
    assert service.grant_called["source_type"] == "coupon"
    assert service.audit["action"] == "redeem_coupon"


def test_create_trial_grant_grants_credits_and_audits():
    from src.productization.service import ProductOpsService

    class StubService(ProductOpsService):
        def __init__(self):
            self.session = type("Session", (), {"execute": AsyncMock(), "commit": AsyncMock()})()
            super().__init__(self.session)
            self.grant_called = None
            self.audit = None

        async def grant_user_credits(self, user_id: str, credits: int, **kwargs):
            self.grant_called = {"user_id": user_id, "credits": credits, **kwargs}
            return {"user_id": user_id}

        async def create_ops_audit_log(self, **kwargs):
            self.audit = kwargs
            return {"audit_log_id": "a1"}

        async def _fetch_one_or_none(self, query, params=None):
            return {"id": params["id"], "user_id": "u1", "credits_amount": 20}

    service = StubService()
    result = asyncio.run(service.create_trial_grant(user_id="u1", credits_amount=20, actor_user_id="ops-1", reason="trial"))
    assert result["credits_amount"] == 20
    assert service.grant_called["source_type"] == "trial_grant"
    assert service.audit["action"] == "create_trial_grant"


def test_review_appeal_record_updates_status_and_audits():
    from src.productization.service import ProductOpsService

    class StubService(ProductOpsService):
        def __init__(self):
            self.session = type("Session", (), {"execute": AsyncMock(), "commit": AsyncMock()})()
            super().__init__(self.session)
            self.audit = None
            self.calls = 0

        async def get_appeal_record(self, appeal_id: str):
            self.calls += 1
            if self.calls == 1:
                return {"id": appeal_id, "user_id": "u1", "status": "pending"}
            return {"id": appeal_id, "user_id": "u1", "status": "approved"}

        async def create_ops_audit_log(self, **kwargs):
            self.audit = kwargs
            return {"audit_log_id": "a1"}

    service = StubService()
    result = asyncio.run(service.review_appeal_record("appeal-1", approved=True, reviewer_user_id="ops-1", decision_note="ok"))
    assert result["status"] == "approved"
    assert service.audit["action"] == "review_appeal"


def test_process_payment_callback_marks_order_paid_and_audits():
    from src.productization.service import ProductOpsService

    class StubService(ProductOpsService):
        def __init__(self):
            self.session = type("Session", (), {"execute": AsyncMock(), "commit": AsyncMock()})()
            super().__init__(self.session)
            self.audit = None

        async def record_payment_callback(self, **kwargs):
            return {"id": "cb-1", **kwargs}

        async def get_payment_order(self, order_id: str):
            return {
                "id": order_id,
                "user_id": "u1",
                "plan_id": "plan-1",
                "order_type": "subscription",
                "amount_cents": 9900,
                "currency": "CNY",
                "provider": "wechat",
                "provider_order_id": "wx-1",
                "credits_delta": 10,
                "detail": {},
            }

        async def create_payment_order(self, user_id: str, **kwargs):
            return {"id": kwargs["order_id"], "status": kwargs["status"], "user_id": user_id}

        async def get_payment_callback_event(self, callback_id: str):
            return {"id": callback_id, "status": "processed"}

        async def create_ops_audit_log(self, **kwargs):
            self.audit = kwargs
            return {"audit_log_id": "a1"}

    service = StubService()
    result = asyncio.run(
        service.process_payment_callback(
            provider="wechat",
            order_id="order-1",
            payment_status="paid",
            amount_cents=9900,
            actor_user_id="ops-1",
        )
    )
    assert result["order"]["status"] == "paid"
    assert service.audit["action"] == "process_payment_callback"


def test_update_risk_case_updates_status_and_audits():
    from src.productization.service import ProductOpsService

    class StubService(ProductOpsService):
        def __init__(self):
            self.session = type("Session", (), {"execute": AsyncMock(), "commit": AsyncMock()})()
            super().__init__(self.session)
            self.audit = None
            self.calls = 0

        async def get_risk_case(self, case_id: str):
            self.calls += 1
            if self.calls == 1:
                return {"id": case_id, "user_id": "u1", "status": "open"}
            return {"id": case_id, "user_id": "u1", "status": "resolved"}

        async def create_ops_audit_log(self, **kwargs):
            self.audit = kwargs
            return {"audit_log_id": "a1"}

    service = StubService()
    result = asyncio.run(service.update_risk_case("risk-1", status="resolved", resolution="done", actor_user_id="ops-1"))
    assert result["status"] == "resolved"
    assert service.audit["action"] == "update_risk_case"


def test_publish_config_release_snapshots_template_and_audits():
    from src.productization.service import ProductOpsService

    class StubService(ProductOpsService):
        def __init__(self):
            self.session = type("Session", (), {"execute": AsyncMock(), "commit": AsyncMock()})()
            super().__init__(self.session)
            self.audit = None

        async def get_template(self, template_id: str):
            return {"id": template_id, "name": "欢迎词", "content": "hello", "category": "growth"}

        async def get_config_release(self, release_id: str):
            return {"id": release_id, "release_type": "template", "target_id": "tpl-1", "status": "published"}

        async def create_ops_audit_log(self, **kwargs):
            self.audit = kwargs
            return {"audit_log_id": "a1"}

    service = StubService()
    result = asyncio.run(service.publish_config_release(release_type="template", target_id="tpl-1", actor_user_id="ops-1", change_note="update"))
    assert result["status"] == "published"
    assert service.audit["action"] == "publish_config_release"


def test_publish_and_rollback_managed_config_release():
    from src.productization.service import ProductOpsService

    class StubService(ProductOpsService):
        def __init__(self):
            self.session = type("Session", (), {"execute": AsyncMock(), "commit": AsyncMock()})()
            super().__init__(self.session)
            self.audit = []
            self.releases = {}

        async def get_config_release(self, release_id: str):
            return self.releases.get(release_id)

        async def create_ops_audit_log(self, **kwargs):
            self.audit.append(kwargs)
            return {"audit_log_id": "a1"}

    service = StubService()
    published = asyncio.run(
        service.publish_config_release(
            release_type="system_config",
            target_id="",
            actor_user_id="ops-1",
            change_note="ops update",
        )
    )
    assert published["id"]
    publish_call = service.session.execute.await_args_list[0]
    publish_params = publish_call.args[1]
    assert publish_params["release_type"] == "system_config"
    assert publish_params["target_id"] == "global"
    service.releases[publish_params["id"]] = {
        "id": publish_params["id"],
        "release_type": publish_params["release_type"],
        "target_id": publish_params["target_id"],
        "snapshot": {"site_name": "星期五"},
        "status": "published",
    }
    rolled = asyncio.run(service.rollback_config_release(publish_params["id"], actor_user_id="ops-1", change_note="revert"))
    rollback_call = service.session.execute.await_args_list[1]
    rollback_params = rollback_call.args[1]
    service.releases[rollback_params["id"]] = {
        "id": rollback_params["id"],
        "release_type": rollback_params["release_type"],
        "target_id": rollback_params["target_id"],
        "snapshot": {"site_name": "星期五"},
        "status": "rolled_back",
    }
    rolled = asyncio.run(service.get_config_release(rollback_params["id"]))
    assert rolled["id"] == rollback_params["id"]
    assert service.audit[0]["action"] == "publish_config_release"
    assert service.audit[1]["action"] == "rollback_config_release"
