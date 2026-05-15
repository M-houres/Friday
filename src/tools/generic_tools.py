"""Built-in generic tools for common cross-app capabilities."""

from __future__ import annotations

import csv
import html
import json
import mimetypes
import re
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from src.artifacts import artifact_service
from src.models.base import Message
from src.models.router import model_router
from src.productization.content_ops import ContentOpsMixin
from src.tools.registry import tool

_STORAGE_ROOT = Path(__file__).resolve().parents[2] / "data" / "storage"
_STORAGE_ROOT.mkdir(parents=True, exist_ok=True)
_DOCUMENT_ALLOWED_ROOTS = [
    Path.cwd().resolve(),
    _STORAGE_ROOT.resolve(),
    artifact_service.root_dir.resolve(),
]


def _safe_storage_path(relative_path: str) -> Path:
    cleaned = str(relative_path or "").strip().replace("\\", "/").lstrip("/")
    if not cleaned:
        raise ValueError("RELATIVE_PATH_REQUIRED")
    path = (_STORAGE_ROOT / cleaned).resolve()
    root = _STORAGE_ROOT.resolve()
    if root != path and root not in path.parents:
        raise ValueError("INVALID_STORAGE_PATH")
    return path


def _extract_html_text(content: str) -> str:
    content = re.sub(r"<script[\s\S]*?</script>", " ", content, flags=re.IGNORECASE)
    content = re.sub(r"<style[\s\S]*?</style>", " ", content, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", content)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _strip_json_code_fence(content: str) -> str:
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def _ensure_document_path_allowed(path: Path) -> Path:
    source = path.expanduser().resolve()
    for root in _DOCUMENT_ALLOWED_ROOTS:
        if source == root or root in source.parents:
            return source
    raise ValueError("DOCUMENT_PATH_NOT_ALLOWED")


def _parse_document(path: Path, content_type: str = "") -> dict[str, Any]:
    suffix = path.suffix.lower()
    resolved_content_type = content_type or mimetypes.guess_type(str(path))[0] or ""

    if suffix in {".txt", ".md", ".py", ".jsonl", ".yaml", ".yml"} or resolved_content_type.startswith("text/"):
        text = path.read_text(encoding="utf-8")
        return {
            "text": text,
            "pages": [{"index": 1, "text": text}],
            "tables": [],
            "metadata": {"content_type": resolved_content_type or "text/plain"},
        }

    if suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        return {
            "text": text,
            "pages": [{"index": 1, "text": text}],
            "tables": [],
            "metadata": {"content_type": "application/json", "keys": list(payload.keys()) if isinstance(payload, dict) else []},
        }

    if suffix == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            reader = list(csv.DictReader(fh))
        return {
            "text": json.dumps(reader, ensure_ascii=False, indent=2),
            "pages": [{"index": 1, "text": json.dumps(reader, ensure_ascii=False)}],
            "tables": [{"name": path.name, "rows": reader}],
            "metadata": {"content_type": "text/csv", "row_count": len(reader)},
        }

    if suffix == ".html":
        raw = path.read_text(encoding="utf-8")
        text = _extract_html_text(raw)
        return {
            "text": text,
            "pages": [{"index": 1, "text": text}],
            "tables": [],
            "metadata": {"content_type": "text/html"},
        }

    raise ValueError(f"UNSUPPORTED_DOCUMENT_TYPE:{suffix or resolved_content_type or 'unknown'}")


def _table_rows_to_text(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return ""
    return json.dumps(rows, ensure_ascii=False, indent=2)


@tool(
    name="http_request",
    description="Send a controlled HTTP request to an external API or webhook and return a structured response.",
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string"},
            "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"]},
            "headers": {"type": "object"},
            "query": {"type": "object"},
            "json_body": {"type": "object"},
            "text_body": {"type": "string"},
            "timeout_s": {"type": "number"},
        },
        "required": ["url"],
    },
    requires_approval=False,
    timeout_ms=45000,
)
async def http_request(
    *,
    url: str,
    method: str = "GET",
    headers: dict[str, Any] | None = None,
    query: dict[str, Any] | None = None,
    json_body: dict[str, Any] | list[Any] | None = None,
    text_body: str = "",
    timeout_s: float = 20.0,
) -> dict[str, Any]:
    resolved_method = method.upper().strip() or "GET"
    if not url.startswith(("http://", "https://")):
        raise ValueError("INVALID_URL")

    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_s, connect=min(timeout_s, 10.0))) as client:
        response = await client.request(
            resolved_method,
            url,
            headers={str(k): str(v) for k, v in dict(headers or {}).items()},
            params=dict(query or {}),
            json=json_body,
            content=text_body.encode("utf-8") if text_body and json_body is None else None,
        )

    content_type = response.headers.get("content-type", "")
    parsed_json = None
    if "json" in content_type:
        try:
            parsed_json = response.json()
        except Exception:
            parsed_json = None

    return {
        "url": str(response.request.url),
        "method": resolved_method,
        "status_code": response.status_code,
        "ok": response.is_success,
        "headers": dict(response.headers),
        "content_type": content_type,
        "text": response.text[:20000],
        "json": parsed_json,
    }


@tool(
    name="web_fetch",
    description="Fetch a public web page and extract normalized readable text and basic metadata.",
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string"},
            "timeout_s": {"type": "number"},
        },
        "required": ["url"],
    },
    is_expensive=False,
    timeout_ms=45000,
)
async def web_fetch(*, url: str, timeout_s: float = 20.0) -> dict[str, Any]:
    result = await http_request(url=url, method="GET", timeout_s=timeout_s)
    text = _extract_html_text(result.get("text") or "")
    parsed = urlparse(url)
    title_match = re.search(r"<title[^>]*>(.*?)</title>", result.get("text") or "", flags=re.IGNORECASE | re.DOTALL)
    return {
        "url": result["url"],
        "status_code": result["status_code"],
        "ok": result["ok"],
        "domain": parsed.netloc,
        "title": html.unescape(title_match.group(1)).strip() if title_match else "",
        "text": text,
        "content_type": result.get("content_type") or "text/html",
    }


@tool(
    name="document_parse",
    description="Parse a supported local document file into normalized text, pages, tables and metadata.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "content_type": {"type": "string"},
        },
        "required": ["path"],
    },
)
def document_parse(*, path: str, content_type: str = "") -> dict[str, Any]:
    source = _ensure_document_path_allowed(Path(path))
    if not source.exists() or not source.is_file():
        raise ValueError("DOCUMENT_NOT_FOUND")
    parsed = _parse_document(source, content_type)
    parsed["path"] = str(source)
    parsed["filename"] = source.name
    return parsed


@tool(
    name="structured_extract",
    description="Use the configured model router to extract structured JSON from text according to an extraction schema.",
    parameters={
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "schema": {"type": "object"},
            "instruction": {"type": "string"},
            "model": {"type": "string"},
        },
        "required": ["text", "schema"],
    },
    is_expensive=True,
    timeout_ms=60000,
)
async def structured_extract(
    *,
    text: str,
    schema: dict[str, Any],
    instruction: str = "",
    model: str = "",
) -> dict[str, Any]:
    if not text.strip():
        raise ValueError("TEXT_REQUIRED")
    if not isinstance(schema, dict) or not schema:
        raise ValueError("SCHEMA_REQUIRED")

    system_prompt = (
        "You extract structured data. "
        "Return a valid JSON object only. "
        "Do not wrap the JSON in markdown."
    )
    user_prompt = (
        f"Extraction instruction:\n{instruction or 'Extract the requested fields faithfully.'}\n\n"
        f"Target schema:\n{json.dumps(schema, ensure_ascii=False, indent=2)}\n\n"
        f"Source text:\n{text[:20000]}"
    )
    response = await model_router.chat(
        [
            Message(role="system", content=system_prompt),
            Message(role="user", content=user_prompt),
        ],
        model=model or None,
        temperature=0.1,
        response_format={"type": "json_object"},
    )
    try:
        payload = json.loads(_strip_json_code_fence(response.content))
    except Exception as exc:
        raise ValueError(f"STRUCTURED_EXTRACT_INVALID_JSON:{exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("STRUCTURED_EXTRACT_NON_OBJECT")
    return {
        "data": payload,
        "model": response.model,
        "tokens_used": response.tokens_used,
        "finish_reason": response.finish_reason,
    }


@tool(
    name="artifact_write",
    description="Write content into a managed artifact and return the downloadable artifact record.",
    parameters={
        "type": "object",
        "properties": {
            "workflow_id": {"type": "string"},
            "filename": {"type": "string"},
            "content": {"type": "string"},
            "content_type": {"type": "string"},
            "owner_user_id": {"type": "string"},
        },
        "required": ["workflow_id", "filename", "content"],
    },
)
def artifact_write(
    *,
    workflow_id: str,
    filename: str,
    content: str,
    content_type: str = "",
    owner_user_id: str = "default",
) -> dict[str, Any]:
    suffix = Path(filename).suffix or ".txt"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=suffix, delete=False) as fh:
        fh.write(content)
        temp_path = fh.name
    try:
        artifact = artifact_service.create_from_file(
            workflow_id=workflow_id,
            filename=filename,
            source_path=temp_path,
            owner_user_id=owner_user_id,
            content_type=content_type or None,
        )
    finally:
        Path(temp_path).unlink(missing_ok=True)
    return artifact


@tool(
    name="template_render",
    description="Render a text template using provided variables, with optional strict missing-variable enforcement.",
    parameters={
        "type": "object",
        "properties": {
            "template": {"type": "string"},
            "variables": {"type": "object"},
            "strict": {"type": "boolean"},
        },
        "required": ["template"],
    },
)
def template_render(
    *,
    template: str,
    variables: dict[str, Any] | None = None,
    strict: bool = False,
) -> dict[str, Any]:
    variables = dict(variables or {})
    rendered = ContentOpsMixin.render_template_content(template, variables)
    if strict:
        unresolved = re.findall(r"{{\s*([^}]+?)\s*}}", rendered)
        if unresolved:
            raise ValueError(f"TEMPLATE_VARIABLES_MISSING:{','.join(sorted(set(unresolved)))}")
    return {"template": template, "variables": variables, "rendered": rendered}


@tool(
    name="storage",
    description="Read, write and list files in the managed local storage area for reusable intermediate data.",
    parameters={
        "type": "object",
        "properties": {
            "operation": {"type": "string", "enum": ["write_text", "write_json", "read_text", "read_json", "list"]},
            "path": {"type": "string"},
            "text": {"type": "string"},
            "data": {"type": "object"},
        },
        "required": ["operation", "path"],
    },
)
def storage(
    *,
    operation: str,
    path: str,
    text: str = "",
    data: dict[str, Any] | list[Any] | None = None,
) -> dict[str, Any]:
    target = _safe_storage_path(path)
    op = operation.strip().lower()

    if op == "write_text":
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return {"operation": op, "path": str(target), "bytes_written": len(text.encode("utf-8"))}

    if op == "write_json":
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(data if data is not None else {}, ensure_ascii=False, indent=2) + "\n"
        target.write_text(payload, encoding="utf-8")
        return {"operation": op, "path": str(target), "bytes_written": len(payload.encode("utf-8"))}

    if op == "read_text":
        if not target.exists():
            raise ValueError("STORAGE_NOT_FOUND")
        content = target.read_text(encoding="utf-8")
        return {"operation": op, "path": str(target), "text": content}

    if op == "read_json":
        if not target.exists():
            raise ValueError("STORAGE_NOT_FOUND")
        return {"operation": op, "path": str(target), "data": json.loads(target.read_text(encoding="utf-8"))}

    if op == "list":
        base = target if target.exists() and target.is_dir() else target.parent
        base.mkdir(parents=True, exist_ok=True)
        items = []
        for item in sorted(base.iterdir(), key=lambda entry: entry.name.lower()):
            items.append(
                {
                    "name": item.name,
                    "path": str(item),
                    "is_dir": item.is_dir(),
                    "size_bytes": item.stat().st_size if item.is_file() else 0,
                }
            )
        return {"operation": op, "path": str(base), "items": items}

    raise ValueError(f"UNSUPPORTED_STORAGE_OPERATION:{operation}")


@tool(
    name="tabular_transform",
    description="Filter, project and aggregate tabular row data represented as a list of JSON objects.",
    parameters={
        "type": "object",
        "properties": {
            "rows": {"type": "array"},
            "operation": {"type": "string", "enum": ["select", "filter_equals", "sum", "count"]},
            "columns": {"type": "array", "items": {"type": "string"}},
            "field": {"type": "string"},
            "value": {},
        },
        "required": ["rows", "operation"],
    },
)
def tabular_transform(
    *,
    rows: list[dict[str, Any]],
    operation: str,
    columns: list[str] | None = None,
    field: str = "",
    value: Any = None,
) -> dict[str, Any]:
    normalized_rows = [dict(row) for row in rows if isinstance(row, dict)]
    op = operation.strip().lower()

    if op == "select":
        selected_columns = [str(item) for item in list(columns or []) if str(item)]
        if not selected_columns:
            raise ValueError("COLUMNS_REQUIRED")
        projected = [{column: row.get(column) for column in selected_columns} for row in normalized_rows]
        return {"operation": op, "rows": projected, "row_count": len(projected)}

    if op == "filter_equals":
        if not field:
            raise ValueError("FIELD_REQUIRED")
        filtered = [row for row in normalized_rows if row.get(field) == value]
        return {"operation": op, "rows": filtered, "row_count": len(filtered)}

    if op == "sum":
        if not field:
            raise ValueError("FIELD_REQUIRED")
        total = 0.0
        for row in normalized_rows:
            item = row.get(field)
            if item in (None, ""):
                continue
            total += float(item)
        return {"operation": op, "field": field, "value": total, "row_count": len(normalized_rows)}

    if op == "count":
        return {"operation": op, "value": len(normalized_rows)}

    raise ValueError(f"UNSUPPORTED_TABULAR_OPERATION:{operation}")
