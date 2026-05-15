"""基础测试 —— 验证核心模块可导入和运行"""

import asyncio

import pytest


class TestConfig:
    def test_settings_load(self):
        from src.config import settings
        assert settings.default_model == "deepseek-chat"
        assert settings.port == 8000

    def test_prod_settings_can_be_constructed(self):
        from src.config import Settings

        prod = Settings(environment="prod", auth_mode="api_key", api_keys="key-1")
        assert prod.environment == "prod"
        assert prod.auth_mode == "api_key"


class TestAppDiscovery:
    def test_skill_module_discovery(self):
        import app

        modules = app._discover_skill_modules("")
        assert "skills.ppt_skill" in modules
        assert "skills.legal_skill" in modules


class TestProjectRegistry:
    def test_project_registry_loads_defaults(self):
        from src.projects.registry import project_registry

        project_registry.load()
        project = project_registry.get_project_manifest("default")
        assert project is not None
        assert "一秒PPT" in project["skills"]
        assert project["home_route"] == "/"
        assert any(page["route"] == "/" for page in project["pages"])
        assert any(page["route"] == "/ppt" for page in project["pages"])

        manifest = project_registry.get_skill_manifest("一秒PPT")
        assert manifest is not None
        assert manifest["route"] == "/ppt"

    def test_project_registry_page_lookup(self):
        from src.projects.registry import project_registry

        project_registry.load()
        page = project_registry.get_page_by_route("/legal")
        assert page is not None
        assert page["project_id"] == "default"
        assert "法务审查" in page["skills"]


class TestDAG:
    def test_dag_creation(self):
        from src.orchestration.dag import DAG, DAGNode
        dag = DAG()
        dag.add_node(DAGNode(node_id="a", task="任务A", dependencies=[]))
        dag.add_node(DAGNode(node_id="b", task="任务B", dependencies=["a"]))
        dag.add_edge("a", "b")

        assert len(dag.nodes) == 2
        assert dag.nodes["b"].dependencies == ["a"]

    def test_topological_order(self):
        from src.orchestration.dag import DAG, DAGNode
        dag = DAG()
        dag.add_node(DAGNode(node_id="1", task="T1", dependencies=[]))
        dag.add_node(DAGNode(node_id="2", task="T2", dependencies=["1"]))
        dag.add_node(DAGNode(node_id="3", task="T3", dependencies=["1"]))
        dag.add_edge("1", "2")
        dag.add_edge("1", "3")

        order = dag.topological_order()
        assert order[0] == "1"
        assert "2" in order
        assert "3" in order

    def test_get_ready_nodes(self):
        from src.orchestration.dag import DAG, DAGNode, NodeStatus
        dag = DAG()
        dag.add_node(DAGNode(node_id="a", task="A", dependencies=[]))
        dag.add_node(DAGNode(node_id="b", task="B", dependencies=["a"]))
        dag.add_edge("a", "b")

        ready = dag.get_ready_nodes()
        assert len(ready) == 1
        assert ready[0].node_id == "a"

        dag.nodes["a"].status = NodeStatus.COMPLETED
        ready = dag.get_ready_nodes()
        assert len(ready) == 1
        assert ready[0].node_id == "b"


class TestCircuitBreaker:
    def test_initial_state(self):
        from src.models.circuit_breaker import CircuitBreaker, CircuitState
        cb = CircuitBreaker("test", "test-model")
        assert cb.state == CircuitState.CLOSED

    def test_before_call_allows(self):
        from src.models.circuit_breaker import CircuitBreaker
        cb = CircuitBreaker("test", "test-model")
        assert asyncio.run(cb.before_call()) is True

    def test_failure_tracking(self):
        from src.models.circuit_breaker import CircuitBreaker, CircuitState
        cb = CircuitBreaker("test", "test-model")
        async def run_failures():
            for _ in range(10):
                await cb.on_failure("500")

        asyncio.run(run_failures())
        assert cb.state == CircuitState.OPEN


class TestIdempotency:
    def test_key_generation(self):
        from src.tools.idempotency import IdempotencyGuard
        key1 = IdempotencyGuard.generate_key("wf1", "step1", "search", {"q": "test"})
        key2 = IdempotencyGuard.generate_key("wf1", "step1", "search", {"q": "test"})
        key3 = IdempotencyGuard.generate_key("wf1", "step2", "search", {"q": "test"})
        assert key1 == key2
        assert key1 != key3
        assert len(key1) == 64  # SHA256 hex

    def test_key_is_stable(self):
        from src.tools.idempotency import IdempotencyGuard
        # 不同参数顺序应产生相同 key
        key1 = IdempotencyGuard.generate_key("wf", "s1", "t", {"a": 1, "b": 2})
        key2 = IdempotencyGuard.generate_key("wf", "s1", "t", {"b": 2, "a": 1})
        assert key1 == key2


class TestToolRegistry:
    def test_register_tool(self):
        from src.tools.registry import ToolRegistry, ToolDefinition
        registry = ToolRegistry()

        def dummy_handler():
            return "ok"

        registry.register(ToolDefinition(
            name="test_tool",
            description="A test tool",
            parameters={"type": "object", "properties": {}},
            handler=dummy_handler,
        ))

        assert "test_tool" in registry.list_tools()
        assert registry.get_handler("test_tool") is not None

    def test_get_schema(self):
        from src.tools.registry import ToolRegistry, ToolDefinition
        registry = ToolRegistry()
        registry.register(ToolDefinition(
            name="search",
            description="Search files",
            parameters={"type": "object", "properties": {"q": {"type": "string"}}},
            handler=lambda: None,
        ))

        schemas = registry.get_schemas()
        assert len(schemas) == 1
        assert schemas[0]["name"] == "search"
        assert "function" not in schemas[0]  # OpenAI schema adds function wrapper later


class TestArtifactService:
    def test_create_and_get_artifact(self, tmp_path):
        from src.artifacts.service import ArtifactService

        source = tmp_path / "report.md"
        source.write_text("hello", encoding="utf-8")

        service = ArtifactService(root_dir=str(tmp_path / "artifacts"))
        artifact = service.create_from_file(
            workflow_id="wf-1",
            filename="report.md",
            source_path=str(source),
            owner_user_id="u1",
            content_type="text/markdown",
        )

        record = service.get(artifact["artifact_id"])
        assert record is not None
        assert record["workflow_id"] == "wf-1"
        assert record["download_url"].endswith("/download")

    def test_artifact_metadata_survives_service_restart(self, tmp_path):
        from src.artifacts.service import ArtifactService

        source = tmp_path / "report.md"
        source.write_text("hello", encoding="utf-8")
        root_dir = tmp_path / "artifacts"

        created = ArtifactService(root_dir=str(root_dir)).create_from_file(
            workflow_id="wf-2",
            filename="report.md",
            source_path=str(source),
            owner_user_id="u2",
            content_type="text/markdown",
        )

        reloaded = ArtifactService(root_dir=str(root_dir))
        record = reloaded.get(created["artifact_id"])
        assert record is not None
        assert record["workflow_id"] == "wf-2"
        assert record["owner_user_id"] == "u2"

    def test_artifact_access_guard(self):
        from types import SimpleNamespace

        from fastapi import HTTPException

        from src.api.routes import _assert_artifact_access

        request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(auth_mode="api_key")),
            state=SimpleNamespace(user={"sub": "owner-1"}),
            headers={},
        )
        artifact = {"owner_user_id": "owner-1"}
        _assert_artifact_access(request, artifact)

        denied = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(auth_mode="api_key")),
            state=SimpleNamespace(user={"sub": "owner-2"}),
            headers={},
        )
        with pytest.raises(HTTPException) as exc:
            _assert_artifact_access(denied, artifact)
        assert exc.value.status_code == 403


class TestSandboxPool:
    def test_workflow_ownership(self):
        from src.tools.isolated_sandbox import SandboxPool

        async def run():
            pool = SandboxPool(pool_size=1)
            sandbox = await pool.acquire(workflow_id="wf-1")
            same = await pool.acquire(workflow_id="wf-1")
            assert sandbox is same
            assert pool.get("wf-1") is sandbox
            assert sandbox.owner_workflow_id == "wf-1"
            await pool.release(workflow_id="wf-1")
            assert pool.get("wf-1") is None

        asyncio.run(run())

    def test_registry_restores_sandbox_across_pool_instances(self, tmp_path):
        from src.tools.isolated_sandbox import SandboxPool

        async def run():
            registry = tmp_path / "registry"
            pool_a = SandboxPool(pool_size=1, registry_root=str(registry))
            sandbox = await pool_a.acquire(workflow_id="wf-restore")
            await sandbox.write_file("output/result.txt", "ok")

            pool_b = SandboxPool(pool_size=0, registry_root=str(registry))
            restored = pool_b.get("wf-restore")
            assert restored is not None
            assert restored.sandbox_id == sandbox.sandbox_id
            assert await restored.read_file("output/result.txt") == "ok"

            await pool_a.release(workflow_id="wf-restore")

        asyncio.run(run())


class TestRetry:
    def test_no_retry_on_400(self):
        from src.models.retry import RetryPolicy
        policy = RetryPolicy(max_retries=3)
        assert policy.is_retryable(400) is False
        assert policy.is_retryable(401) is False

    def test_retry_on_429(self):
        from src.models.retry import RetryPolicy
        policy = RetryPolicy(max_retries=3)
        assert policy.is_retryable(429) is True

    def test_retry_on_5xx(self):
        from src.models.retry import RetryPolicy
        policy = RetryPolicy(max_retries=3)
        assert policy.is_retryable(500) is True
        assert policy.is_retryable(503) is True

    def test_delay_backoff(self):
        from src.models.retry import RetryPolicy
        policy = RetryPolicy(max_retries=3, jitter=False)
        d1 = policy.delay(0)
        d2 = policy.delay(1)
        d3 = policy.delay(2)
        assert d1 <= d2 <= d3
