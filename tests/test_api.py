"""API 集成测试 —— Smoke tests for all endpoints"""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """同步 smoke test 客户端。"""
    from src.main import app
    with TestClient(app) as test_client:
        yield test_client


class TestHealth:
    def test_app_starts_when_database_is_unavailable(self, monkeypatch: pytest.MonkeyPatch):
        from src.main import app

        async def fail_init_db():
            raise ConnectionRefusedError("database unavailable")

        monkeypatch.setattr("src.main.init_db", fail_init_db)

        with TestClient(app) as test_client:
            response = test_client.get("/")

        assert response.status_code == 200
        assert app.state.database_available is False

    def test_live(self, client: TestClient):
        response = client.get("/api/v1/health/live")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_root(self, client: TestClient):
        response = client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert data["name"].startswith("星期五")
        assert data["version"] == "1.0.0"

    def test_docs(self, client: TestClient):
        response = client.get("/docs")
        assert response.status_code == 200

    def test_panel(self, client: TestClient):
        response = client.get("/panel")
        assert response.status_code == 200
        assert "星期五 v1.0 -- AI 产品运营控制台" in response.text


class TestTools:
    def test_list_tools(self, client: TestClient):
        response = client.get("/api/v1/tools")
        assert response.status_code == 200
        data = response.json()
        assert "tools" in data

    def test_tool_schema_not_found(self, client: TestClient):
        response = client.get("/api/v1/tools/nonexistent/schema")
        assert response.status_code == 404


class TestSkills:
    def test_list_skills(self, client: TestClient):
        response = client.get("/api/v1/skills")
        assert response.status_code == 200
        data = response.json()
        assert "skills" in data

    def test_skill_not_found(self, client: TestClient):
        response = client.get("/api/v1/skills/nonexistent")
        assert response.status_code == 404


class TestComponents:
    def test_list_components(self, client: TestClient):
        response = client.get("/api/v1/components")
        assert response.status_code == 200

    def test_component_manifest(self, client: TestClient):
        response = client.get("/api/v1/components/manifest")
        assert response.status_code == 200
        data = response.json()
        assert "components" in data
        assert "defaultComponents" in data


class TestMemory:
    def test_memory_stats(self, client: TestClient):
        response = client.get("/api/v1/memory/stats")
        assert response.status_code == 200

    def test_memory_context(self, client: TestClient):
        response = client.get("/api/v1/memory/context/test_user?task=test")
        assert response.status_code == 200


class TestTopics:
    def test_topic_stats(self, client: TestClient):
        response = client.get("/api/v1/topics")
        assert response.status_code == 200


class TestDurable:
    def test_durable_stats(self, client: TestClient):
        response = client.get("/api/v1/durable")
        assert response.status_code == 200


class TestGuardrails:
    def test_guardrail_stats(self, client: TestClient):
        response = client.get("/api/v1/guardrails")
        assert response.status_code == 200


class TestConfig:
    def test_config_list(self, client: TestClient):
        response = client.get("/api/v1/config")
        assert response.status_code == 200


class TestAgentTools:
    def test_agent_tools(self, client: TestClient):
        response = client.get("/api/v1/agent-tools")
        assert response.status_code == 200


class TestProductizationEndpoints:
    def test_jobs_endpoint(self, client: TestClient):
        response = client.get("/api/v1/jobs")
        assert response.status_code == 200

    def test_templates_endpoint(self, client: TestClient):
        response = client.get("/api/v1/templates")
        assert response.status_code == 200

    def test_knowledge_endpoint(self, client: TestClient):
        response = client.get("/api/v1/knowledge")
        assert response.status_code == 200

    def test_ops_summary_endpoint(self, client: TestClient):
        response = client.get("/api/v1/ops/summary")
        assert response.status_code == 200
