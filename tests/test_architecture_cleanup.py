import asyncio
from pathlib import Path


def test_ops_routes_use_domain_facades_instead_of_legacy_service():
    source = Path("src/api/ops_routes.py").read_text(encoding="utf-8")

    assert "from src.productization.service import ProductOpsService" not in source
    assert "ProductOpsService(" not in source
    assert "BillingOpsService(db).list_payment_orders" in source
    assert "GrowthOpsService(db).list_growth_coupons" in source
    assert "ContentOpsService(db).list_templates" in source
    assert "WorkflowOpsService(db).list_async_jobs" in source
    assert "SupportOpsService(db).list_support_tickets" in source
    assert "AuditOpsService(db).list_config_releases" in source


def test_tool_harness_times_out_sync_handlers():
    from src.tools.harness import ToolHarness
    from src.tools.registry import ToolDefinition, ToolRegistry

    registry = ToolRegistry()

    def slow_tool():
        import time

        time.sleep(0.2)
        return {"ok": True}

    registry.register(
        ToolDefinition(
            name="slow_sync_tool",
            description="slow",
            parameters={"type": "object", "properties": {}},
            handler=slow_tool,
            timeout_ms=50,
        )
    )

    result = asyncio.run(ToolHarness(registry=registry).execute("slow_sync_tool", {}))

    assert result["success"] is False
    assert result["error"] == "Tool timed out"


def test_hands_uses_tool_harness_with_idempotency_cache():
    from src.core.hands import Hands
    from src.tools.idempotency import IdempotencyGuard
    from src.tools.registry import ToolDefinition, ToolRegistry

    registry = ToolRegistry()
    calls = {"count": 0}

    def add_one(value: int):
        calls["count"] += 1
        return {"value": value + 1}

    registry.register(
        ToolDefinition(
            name="add_one",
            description="add one",
            parameters={"type": "object", "properties": {"value": {"type": "integer"}}},
            handler=add_one,
            timeout_ms=1000,
        )
    )

    class MemoryIdempotencyGuard(IdempotencyGuard):
        def __init__(self):
            super().__init__()
            self.cache: dict[str, object] = {}
            self.in_progress: set[str] = set()

        async def check(self, key: str):
            return self.cache.get(key)

        async def mark_in_progress(self, key: str):
            self.in_progress.add(key)

        async def store(self, key: str, result):
            self.cache[key] = result
            self.in_progress.discard(key)

        async def clear(self, key: str):
            self.cache.pop(key, None)
            self.in_progress.discard(key)

    guard = MemoryIdempotencyGuard()
    hands = Hands(registry, idempotency=guard)

    first = asyncio.run(hands.execute("add_one", {"value": 2}, idempotency_key="ik-1"))
    second = asyncio.run(hands.execute("add_one", {"value": 2}, idempotency_key="ik-1"))

    assert first.success is True
    assert second.success is True
    assert first.data == {"value": 3}
    assert second.data == {"value": 3}
    assert calls["count"] == 1


def test_migration_scaffold_is_usable():
    env_path = Path("migrations/env.py")
    baseline_path = Path("migrations/versions/20260515_000001_baseline.py")
    db_source = Path("src/db.py").read_text(encoding="utf-8")

    env_source = env_path.read_text(encoding="utf-8")
    baseline_source = baseline_path.read_text(encoding="utf-8")

    assert env_path.exists()
    assert "async_engine_from_config" in env_source
    assert "settings.database_url" in env_source
    assert baseline_path.exists()
    assert 'revision = "20260515_000001"' in baseline_source
    assert "from src.db_schema import SCHEMA_STATEMENTS" in baseline_source
    assert "for statement in SCHEMA_STATEMENTS" in baseline_source
    assert "skipping bootstrap schema replay" in db_source
    assert "bootstrapping baseline schema" in db_source


def test_product_service_is_now_a_compatibility_facade():
    service_source = Path("src/productization/service.py").read_text(encoding="utf-8")
    domain_source = Path("src/productization/domain_services.py").read_text(encoding="utf-8")

    assert '"""Product operations compatibility facade."""' in service_source
    assert "class ProductOpsService(" in service_source
    assert "AuditOpsMixin" in service_source
    assert "BillingOpsMixin" in service_source
    assert "WorkflowOpsMixin" in service_source
    assert "from src.productization.service import ProductOpsService" not in domain_source
    assert "class BillingOpsService(BillingOpsMixin" in domain_source


def test_panel_script_is_externalized():
    panel_source = Path("src/api/panel.html").read_text(encoding="utf-8")
    app_js_path = Path("static/panel/app.js")
    app_js_source = app_js_path.read_text(encoding="utf-8")

    assert '<script src="/static/panel/common.js"></script>' in panel_source
    assert '<script src="/static/panel/app.js"></script>' in panel_source
    assert "async function loadOverview()" not in panel_source
    assert app_js_path.exists()
    assert "async function loadOverview()" in app_js_source
    assert "bootPanel();" in app_js_source


def test_isolated_sandbox_pool_get_supports_sandbox_id_lookup():
    from src.tools.isolated_sandbox import IsolatedSandbox, SandboxPool

    pool = SandboxPool(pool_size=0)
    sandbox = IsolatedSandbox()
    sandbox.owner_workflow_id = "wf-1"
    pool._in_use["wf-1"] = sandbox

    assert pool.get("wf-1") is sandbox
    assert pool.get(sandbox.sandbox_id) is sandbox
