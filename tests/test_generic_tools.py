import asyncio
import json
from pathlib import Path


def test_tool_registry_auto_loads_generic_tools():
    from src.tools.registry import tool_registry

    tools = tool_registry.list_tools()

    assert "http_request" in tools
    assert "web_fetch" in tools
    assert "document_parse" in tools
    assert "structured_extract" in tools
    assert "artifact_write" in tools
    assert "storage" in tools
    assert "template_render" in tools
    assert "tabular_transform" in tools


def test_storage_tool_round_trip(tmp_path, monkeypatch):
    from src.tools import generic_tools

    monkeypatch.setattr(generic_tools, "_STORAGE_ROOT", tmp_path)

    write_result = generic_tools.storage(operation="write_text", path="demo/a.txt", text="hello")
    read_result = generic_tools.storage(operation="read_text", path="demo/a.txt")
    list_result = generic_tools.storage(operation="list", path="demo")

    assert write_result["bytes_written"] > 0
    assert read_result["text"] == "hello"
    assert any(item["name"] == "a.txt" for item in list_result["items"])


def test_document_parse_supports_text_and_json(tmp_path):
    from src.tools.generic_tools import document_parse

    safe_root = Path.cwd() / "data" / "storage" / "test-doc-parse"
    safe_root.mkdir(parents=True, exist_ok=True)
    text_path = safe_root / "demo.txt"
    json_path = safe_root / "demo.json"
    text_path.write_text("hello world", encoding="utf-8")
    json_path.write_text(json.dumps({"name": "Friday"}, ensure_ascii=False), encoding="utf-8")

    text_result = document_parse(path=str(text_path))
    json_result = document_parse(path=str(json_path))

    assert "hello world" in text_result["text"]
    assert json_result["metadata"]["content_type"] == "application/json"
    assert "Friday" in json_result["text"]


def test_artifact_write_creates_downloadable_record(tmp_path, monkeypatch):
    from src.artifacts.service import ArtifactService
    from src.tools import generic_tools

    monkeypatch.setattr(generic_tools, "artifact_service", ArtifactService(root_dir=tmp_path))

    record = generic_tools.artifact_write(
        workflow_id="wf-1",
        filename="summary.md",
        content="# Summary",
        owner_user_id="u-1",
    )

    assert record["workflow_id"] == "wf-1"
    assert record["owner_user_id"] == "u-1"
    assert record["download_url"].endswith("/download")


def test_structured_extract_uses_model_router(monkeypatch):
    from src.models.base import ModelResponse
    from src.tools.generic_tools import structured_extract

    async def fake_chat(messages, model=None, temperature=0.7, max_tokens=4096, tools=None, response_format=None):
        return ModelResponse(
            content=json.dumps({"title": "合同", "risk_level": "medium"}, ensure_ascii=False),
            model=model or "fake-model",
            tokens_used=123,
            finish_reason="stop",
        )

    monkeypatch.setattr("src.tools.generic_tools.model_router.chat", fake_chat)

    result = asyncio.run(
        structured_extract(
            text="这是一份合同文本",
            schema={"title": "string", "risk_level": "string"},
            instruction="抽取标题和风险等级",
        )
    )

    assert result["data"]["title"] == "合同"
    assert result["tokens_used"] == 123


def test_template_render_and_tabular_transform():
    from src.tools.generic_tools import tabular_transform, template_render

    rendered = template_render(
        template="你好 {{name}}，你有 {{count}} 个任务。",
        variables={"name": "Friday", "count": 3},
        strict=True,
    )
    selected = tabular_transform(
        rows=[{"name": "A", "amount": 10}, {"name": "B", "amount": 20}],
        operation="select",
        columns=["name"],
    )
    total = tabular_transform(
        rows=[{"name": "A", "amount": 10}, {"name": "B", "amount": 20}],
        operation="sum",
        field="amount",
    )

    assert rendered["rendered"] == "你好 Friday，你有 3 个任务。"
    assert selected["rows"] == [{"name": "A"}, {"name": "B"}]
    assert total["value"] == 30.0


def test_web_fetch_extracts_text(monkeypatch):
    from src.tools.generic_tools import web_fetch

    async def fake_http_request(**kwargs):
        return {
            "url": "https://example.com",
            "status_code": 200,
            "ok": True,
            "content_type": "text/html",
            "text": "<html><head><title>Demo</title></head><body><h1>Hello</h1><p>World</p></body></html>",
            "json": None,
        }

    monkeypatch.setattr("src.tools.generic_tools.http_request", fake_http_request)

    result = asyncio.run(web_fetch(url="https://example.com"))

    assert result["title"] == "Demo"
    assert "Hello World" in result["text"]
