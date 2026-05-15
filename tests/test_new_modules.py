"""Phase 2-4 模块补充测试"""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest


class TestColdStorage:
    def test_cold_storage_init(self):
        from src.memory.cold_storage import S3ColdStorage
        storage = S3ColdStorage(use_local_fs=True, local_path="/tmp/friday_test")
        assert storage.use_local_fs is True
        assert storage.bucket == "friday-cold"


class TestAuth:
    def test_auth_middleware_init(self):
        from src.api.auth import AuthMiddleware
        mw = AuthMiddleware(
            app=None, auth_mode="api_key",
            api_keys={"test-key-1", "test-key-2"},
        )
        assert mw.auth_mode == "api_key"
        assert len(mw.api_keys) == 2

    def test_auth_skip_paths(self):
        from src.api.auth import SKIP_PATHS, SKIP_PREFIXES, DEV_ONLY_SKIP_PATHS
        assert "/" in SKIP_PATHS
        assert "/docs" in DEV_ONLY_SKIP_PATHS
        assert "/panel" in DEV_ONLY_SKIP_PATHS
        assert "/api/v1/health" in SKIP_PREFIXES


class TestPanelModule:
    def test_panel_html_is_loaded_from_file(self):
        from src.api.panel import get_panel_html

        html = get_panel_html()
        assert html.startswith("<!DOCTYPE html>")
        assert "运营控制台" in html


class TestRateLimit:
    def test_rate_limit_middleware_init(self):
        from src.api.ratelimit import RateLimitMiddleware
        mw = RateLimitMiddleware(app=None, global_rpm=100, user_rpm=10, ip_rpm=5)
        assert mw.global_rpm == 100
        assert mw.user_rpm == 10
        assert mw.ip_rpm == 5

    def test_rate_limit_middleware_skips_pytest_runtime(self, monkeypatch):
        from src.api.ratelimit import RateLimitMiddleware

        monkeypatch.setenv("PYTEST_CURRENT_TEST", "tests::demo")
        mw = RateLimitMiddleware(app=None, global_rpm=100, user_rpm=10, ip_rpm=5)
        assert mw._should_skip("/api/v1/jobs") is True


class TestCheckpoint:
    def test_checkpoint_manager_import(self):
        from src.session.checkpoint import CheckpointManager
        assert CheckpointManager is not None


class TestSummarizer:
    def test_summarizer_import(self):
        from src.memory.summarizer import ConversationSummarizer
        summarizer = ConversationSummarizer()
        assert summarizer.max_summary_length == 300

    def test_fallback_summarize(self):
        from src.memory.summarizer import ConversationSummarizer
        summarizer = ConversationSummarizer()
        result = summarizer._fallback_summarize("这是第一句话。这是第二句话。这是第三句话。这是第四句话。")
        assert "第一句话" in result
        assert len(result) < 300

    def test_messages_to_text(self):
        from src.memory.summarizer import ConversationSummarizer
        summarizer = ConversationSummarizer()
        msgs = [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好，有什么可以帮助你？"},
            {"role": "tool", "content": "search result", "name": "search"},
        ]
        text = summarizer._messages_to_text(msgs)
        assert "[用户]" in text
        assert "[AI]" in text
        assert "[工具 search]" in text


class TestSchemas:
    def test_workflow_request(self):
        from src.api.schemas import WorkflowRequest
        req = WorkflowRequest(task="test task", project_id="default", page_id="legal-briefing")
        assert req.task == "test task"
        assert req.mode == "auto"
        assert req.project_id == "default"
        assert req.page_id == "legal-briefing"

    def test_approval_request(self):
        from src.api.schemas import ApprovalRequest
        req = ApprovalRequest(step_id="s1", approved=False, comment="需要修改")
        assert req.approved is False
        assert req.comment == "需要修改"


class TestSkillSystem:
    def test_skill_registry(self):
        from src.tools.skill import skill_registry, FridaySkill, skill, tool

        @skill(
            name="test_skill",
            trigger="测试|test",
            description="A test skill",
        )
        class TestSkill(FridaySkill):
            workflow = [
                {"id": "step1", "tool": "do_stuff", "name": "Do Stuff", "dependencies": []},
            ]

            @tool(name="do_stuff", description="Does stuff", parameters={"type": "object", "properties": {}})
            async def do_stuff(self, task="", context=None):
                return {"done": True}

        instance = TestSkill()
        assert instance.name == "test_skill"
        assert instance.matches_trigger("这是一个测试任务") is True
        assert instance.matches_trigger("unrelated") is False

        schemas = TestSkill.get_tool_schemas()
        assert len(schemas) >= 1

        # Check it's registered
        assert skill_registry.get("test_skill") is not None
        manifest = skill_registry.to_frontend_manifest()
        assert any(s["name"] == "test_skill" for s in manifest["skills"])

    def test_skill_workflow_state_propagation(self):
        from src.tools.skill import FridaySkill, skill, tool

        @skill(
            name="stateful_skill",
            trigger="stateful",
            description="A workflow that requires previous step state",
        )
        class StatefulSkill(FridaySkill):
            workflow = [
                {"id": "step1", "tool": "collect_input", "name": "Collect Input", "dependencies": []},
                {"id": "step2", "tool": "use_previous", "name": "Use Previous", "dependencies": ["step1"]},
            ]

            @tool(name="collect_input", description="collect", parameters={"type": "object", "properties": {}})
            async def collect_input(self, task="", context=None):
                return {"success": True, "data": {"value": task.upper()}}

            @tool(name="use_previous", description="use previous", parameters={"type": "object", "properties": {}})
            async def use_previous(self, task="", context=None):
                first = context["collect_input"]["data"]["value"]
                alias = context["step1"]["data"]["value"]
                return {"success": True, "data": {"combined": f"{first}:{alias}"}}

        instance = StatefulSkill()
        results, state = asyncio.run(instance.execute_workflow("hello"))
        assert results["step2"]["data"]["combined"] == "HELLO:HELLO"
        assert state["collect_input"]["data"]["value"] == "HELLO"
        assert state["step1"]["data"]["value"] == "HELLO"


class TestStreamIsolation:
    def test_stream_channels_are_isolated(self):
        from src.api.stream import FridayStream

        stream = FridayStream()
        queue_a = stream.subscribe("wf-a")
        queue_b = stream.subscribe("wf-b")

        async def run():
            await stream.workflow_step_complete("step-1", {"ok": True}, workflow_id="wf-a")
            event_a = await queue_a.get()
            assert event_a.data["workflowId"] == "wf-a"
            assert event_a.data["output"]["ok"] is True
            assert queue_b.empty() is True

        asyncio.run(run())

        stream.unsubscribe("wf-a", queue_a)
        stream.unsubscribe("wf-b", queue_b)


class TestAuthHelpers:
    def test_public_path_rules(self):
        from src.api.auth import is_public_path

        assert is_public_path("/") is True
        assert is_public_path("/api/v1/health/live") is True


class TestSkillManifestMetadata:
    def test_skill_manifest_in_manifest_output(self):
        import skills.ppt_skill  # noqa: F401
        from src.projects.registry import project_registry
        from src.tools.skill import skill_registry

        project_registry.load()
        skill_registry.apply_manifest_metadata()
        manifest = skill_registry.to_frontend_manifest()

        ppt = next((item for item in manifest["skills"] if item["name"] == "一秒PPT"), None)
        assert ppt is not None
        assert ppt["route"] == "/ppt"
        assert ppt["project"] == "default"


class TestProjectManifestPages:
    def test_project_manifest_contains_pages(self):
        from src.projects.registry import project_registry

        project_registry.load()
        manifest = project_registry.get_project_manifest("default")
        assert manifest is not None
        routes = {page["route"] for page in manifest["pages"]}
        assert "/" in routes
        assert "/ppt" in routes
        assert "/legal" in routes
        scenario_page = next(page for page in manifest["pages"] if page["id"] == "legal-briefing")
        assert len(scenario_page["scenario"]["steps"]) == 2
        assert scenario_page["scenario"]["steps"][1]["skill"] == "一秒PPT"

    def test_project_manifest_normalizes_page_billing(self, tmp_path):
        import json
        from src.projects.registry import ProjectRegistry

        config_dir = tmp_path / "config"
        (config_dir / "projects").mkdir(parents=True)
        (config_dir / "skills").mkdir(parents=True)
        (config_dir / "projects" / "billing.json").write_text(
            json.dumps(
                {
                    "id": "billing-demo",
                    "name": "Billing Demo",
                    "pages": [
                        {
                            "id": "premium-page",
                            "name": "Premium",
                            "route": "/premium",
                            "page": "premium.html",
                            "billing": {"required": True, "credits_cost": 12, "reason": "premium_task"},
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        registry = ProjectRegistry(config_dir=str(config_dir))
        registry.load()
        page = registry.get_page("billing-demo", "premium-page")

        assert page is not None
        assert page["billing"]["required"] is True
        assert page["billing"]["credits_cost"] == 12
        assert page["billing"]["reason"] == "premium_task"


def test_page_scenario_executor_runs_multiple_skills(tmp_path, monkeypatch):
    import json
    from src.tools.skill import FridaySkill, skill, tool
    from src.projects.registry import ProjectRegistry
    import src.orchestration.page_scenario as page_scenario_module

    @skill(name="scenario_skill_alpha", trigger="alpha", description="alpha")
    class ScenarioSkillAlpha(FridaySkill):
        workflow = [
            {"id": "collect", "tool": "collect", "name": "Collect", "dependencies": []},
            {"id": "deliver", "tool": "deliver", "name": "Deliver", "dependencies": ["collect"]},
        ]

        @tool(name="collect", description="collect", parameters={"type": "object", "properties": {}})
        async def collect(self, task="", context=None):
            return {"success": True, "data": {"text": task}}

        @tool(name="deliver", description="deliver", parameters={"type": "object", "properties": {}}, depends_on=["collect"])
        async def deliver(self, task="", context=None):
            return {"success": True, "data": {"summary": f"alpha::{context['collect']['data']['text']}", "filename": "alpha.md", "download_url": "/alpha"}}

    @skill(name="scenario_skill_beta", trigger="beta", description="beta")
    class ScenarioSkillBeta(FridaySkill):
        workflow = [
            {"id": "deliver", "tool": "deliver", "name": "Deliver", "dependencies": []},
        ]

        @tool(name="deliver", description="deliver", parameters={"type": "object", "properties": {}})
        async def deliver(self, task="", context=None):
            return {"success": True, "data": {"summary": f"beta::{task}", "filename": "beta.md", "download_url": "/beta"}}

    config_dir = tmp_path / "config"
    (config_dir / "projects").mkdir(parents=True)
    (config_dir / "skills").mkdir(parents=True)
    (config_dir / "projects" / "scenario.json").write_text(
        json.dumps(
            {
                "kind": "project",
                "id": "scenario",
                "name": "Scenario Product",
                "pages": [
                    {
                        "id": "combo",
                        "name": "Combo",
                        "route": "/combo",
                        "page": "combo.html",
                        "skills": ["scenario_skill_alpha", "scenario_skill_beta"],
                        "scenario": {
                            "steps": [
                                {"id": "alpha", "name": "Alpha", "skill": "scenario_skill_alpha"},
                                {"id": "beta", "name": "Beta", "skill": "scenario_skill_beta", "task_template": "Wrap {{result:alpha}}"},
                            ]
                        },
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    temp_registry = ProjectRegistry(config_dir=str(config_dir))
    temp_registry.load()
    monkeypatch.setattr(page_scenario_module, "project_registry", temp_registry)

    class DummyDB:
        async def execute(self, *args, **kwargs):
            return None

        async def commit(self):
            return None

    executor = page_scenario_module.PageScenarioExecutor(DummyDB())
    result = asyncio.run(
        executor.execute(
            workflow_id="wf-test",
            project_id="scenario",
            page_id="combo",
            task="合同内容",
            user_id="u1",
            context={},
        )
    )

    payload = result["final_output"]["data"]
    assert "alpha::合同内容" in payload["summary"]
    assert len(payload["steps"]) == 2
    assert payload["downloads"][0]["download_url"] == "/alpha"
    assert payload["downloads"][1]["download_url"] == "/beta"


def test_page_scenario_executor_supports_inputs_outputs_and_fallback(tmp_path, monkeypatch):
    import json
    from src.tools.skill import FridaySkill, skill, tool
    from src.projects.registry import ProjectRegistry
    import src.orchestration.page_scenario as page_scenario_module

    @skill(name="scenario_skill_mapper", trigger="mapper", description="mapper")
    class ScenarioSkillMapper(FridaySkill):
        workflow = [{"id": "deliver", "tool": "deliver", "name": "Deliver", "dependencies": []}]

        @tool(name="deliver", description="deliver", parameters={"type": "object", "properties": {}})
        async def deliver(self, task="", context=None):
            value = context.get("contract_text", task)
            return {"success": True, "data": {"summary": f"mapped::{value}", "score": 88}}

    @skill(name="scenario_skill_broken", trigger="broken", description="broken")
    class ScenarioSkillBroken(FridaySkill):
        workflow = [{"id": "boom", "tool": "boom", "name": "Boom", "dependencies": []}]

        @tool(name="boom", description="boom", parameters={"type": "object", "properties": {}})
        async def boom(self, task="", context=None):
            raise RuntimeError("step failed")

    config_dir = tmp_path / "config"
    (config_dir / "projects").mkdir(parents=True)
    (config_dir / "skills").mkdir(parents=True)
    (config_dir / "projects" / "advanced.json").write_text(
        json.dumps(
            {
                "kind": "project",
                "id": "advanced",
                "name": "Advanced Scenario",
                "pages": [
                    {
                        "id": "review",
                        "name": "Review",
                        "route": "/review",
                        "page": "review.html",
                        "skills": ["scenario_skill_mapper", "scenario_skill_broken"],
                        "scenario": {
                            "steps": [
                                {
                                    "id": "mapped",
                                    "name": "Mapped",
                                    "skill": "scenario_skill_mapper",
                                    "inputs": {"contract_text": "task"},
                                    "outputs": {"review_score": "score"},
                                },
                                {
                                    "id": "fallback",
                                    "name": "Fallback",
                                    "skill": "scenario_skill_broken",
                                    "run_if": "review_score=88",
                                    "fallback_task_template": "fallback::{{result:mapped}}",
                                    "continue_on_error": True,
                                },
                            ]
                        },
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    temp_registry = ProjectRegistry(config_dir=str(config_dir))
    temp_registry.load()
    monkeypatch.setattr(page_scenario_module, "project_registry", temp_registry)

    class DummyDB:
        async def execute(self, *args, **kwargs):
            return None

        async def commit(self):
            return None

    executor = page_scenario_module.PageScenarioExecutor(DummyDB())
    result = asyncio.run(
        executor.execute(
            workflow_id="wf-advanced",
            project_id="advanced",
            page_id="review",
            task="合同文本",
            user_id="u1",
            context={"task": "合同文本"},
        )
    )

    state = result["state"]
    payload = result["final_output"]["data"]
    assert state["review_score"] == 88
    assert payload["status"] in {"completed", "partial"}
    assert state["_scenario_results"]["fallback"]["fallback"] is True
    assert "mapped::合同文本" in payload["summary"]


def test_page_scenario_executor_emits_approval_required_step(tmp_path, monkeypatch):
    import json
    from src.tools.skill import FridaySkill, skill, tool
    from src.projects.registry import ProjectRegistry
    import src.orchestration.page_scenario as page_scenario_module

    @skill(name="scenario_skill_approval", trigger="approval", description="approval")
    class ScenarioSkillApproval(FridaySkill):
        workflow = [{"id": "deliver", "tool": "deliver", "name": "Deliver", "dependencies": []}]

        @tool(name="deliver", description="deliver", parameters={"type": "object", "properties": {}})
        async def deliver(self, task="", context=None):
            return {"success": True, "data": {"summary": "approved later"}}

    config_dir = tmp_path / "config"
    (config_dir / "projects").mkdir(parents=True)
    (config_dir / "skills").mkdir(parents=True)
    (config_dir / "projects" / "approval.json").write_text(
        json.dumps(
            {
                "id": "approval-demo",
                "name": "Approval Demo",
                "pages": [
                    {
                        "id": "approval-page",
                        "name": "Approval Page",
                        "route": "/approval-page",
                        "page": "approval.html",
                        "skills": ["scenario_skill_approval"],
                        "scenario": {
                            "steps": [
                                {
                                    "id": "human_gate",
                                    "name": "人工审核",
                                    "skill": "scenario_skill_approval",
                                    "approval_required": True,
                                    "approval_note": "需要法务确认后才能继续",
                                }
                            ]
                        },
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    temp_registry = ProjectRegistry(config_dir=str(config_dir))
    temp_registry.load()
    monkeypatch.setattr(page_scenario_module, "project_registry", temp_registry)

    class DummyDB:
        async def execute(self, *args, **kwargs):
            return None

        async def commit(self):
            return None

    executor = page_scenario_module.PageScenarioExecutor(DummyDB())
    result = asyncio.run(
        executor.execute(
            workflow_id="wf-approval",
            project_id="approval-demo",
            page_id="approval-page",
            task="审批一下",
            user_id="u1",
            context={},
        )
    )

    payload = result["final_output"]["data"]
    assert payload["status"] == "approval_required"
    assert payload["approvals"][0]["id"] == "human_gate"
    assert result["checkpoint"]["next_step_index"] == 0
    assert "human_gate" not in result["checkpoint"]["scenario_outputs"]


class TestLogger:
    def test_setup_logging(self):
        from src.observability.logger import setup_logging
        import logging
        setup_logging()
        logger = logging.getLogger("test_logger")
        assert logger is not None


class TestDbSchema:
    def test_schema_statements_exist(self):
        from src.db_schema import SCHEMA_STATEMENTS

        assert len(SCHEMA_STATEMENTS) > 10
        assert any("async_jobs" in stmt for stmt in SCHEMA_STATEMENTS)


class TestCoordinatorHelpers:
    def test_normalize_final_result(self):
        from src.orchestration.coordinator_helpers import normalize_final_result

        normalized = normalize_final_result(
            {
                "final_output": {
                    "data": {
                        "summary": "ok",
                        "download_url": "/download",
                    }
                }
            },
            project_id="demo",
            page_id="page-1",
        )
        assert normalized["summary"] == "ok"
        assert normalized["downloads"][0]["download_url"] == "/download"

    def test_extract_approvals(self):
        from src.orchestration.coordinator_helpers import extract_approvals

        approvals = extract_approvals(
            {
                "final_output": {
                    "data": {
                        "approvals": [{"id": "a1"}, {"name": "skip"}],
                    }
                }
            }
        )
        assert len(approvals) == 1
        assert approvals[0]["id"] == "a1"


class TestCoordinatorStore:
    def test_start_node_persists_workflow_node(self):
        from src.orchestration.coordinator_store import CoordinatorStore
        from src.orchestration.dag import DAGNode

        class FakeSession:
            def __init__(self):
                self.execute = AsyncMock()
                self.commit = AsyncMock()

        async def run():
            session = FakeSession()
            store = CoordinatorStore(session)
            node = DAGNode(node_id="node-1", task="do work", dependencies=["dep-1"])
            node_db_id = await store.start_node("wf-1", node)
            return session, node_db_id

        session, node_db_id = asyncio.run(run())
        assert len(node_db_id) == 32
        assert session.execute.await_count == 1
        params = session.execute.await_args_list[0].args[1]
        assert params["wf_id"] == "wf-1"
        assert params["node_id"] == "node-1"
        assert params["deps"] == ["dep-1"]
        session.commit.assert_awaited_once()

    def test_complete_node_serializes_result_without_mutating_input(self):
        from src.orchestration.coordinator_store import CoordinatorStore

        class FakeSession:
            def __init__(self):
                self.execute = AsyncMock()
                self.commit = AsyncMock()

        async def run():
            session = FakeSession()
            store = CoordinatorStore(session)
            result = {"content": "ok", "_tokens": 12, "_model": "spec"}
            await store.complete_node("node-db-1", result, "deepseek-chat")
            return session, result

        session, result = asyncio.run(run())
        params = session.execute.await_args_list[0].args[1]
        assert params["id"] == "node-db-1"
        assert params["tokens"] == 12
        assert json.loads(params["result"]) == {"content": "ok"}
        assert result["_tokens"] == 12
        assert result["_model"] == "spec"
