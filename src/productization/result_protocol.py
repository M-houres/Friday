"""Unified result protocol for pages, skills, and admin views."""

from __future__ import annotations

from datetime import datetime, timezone


def normalize_result_payload(
    payload: dict | None,
    *,
    source: str = "",
    status: str = "completed",
    error: str = "",
    metrics: dict | None = None,
    next_actions: list[str] | None = None,
) -> dict:
    payload = dict(payload or {})
    downloads = _collect_downloads(payload)
    preview = _build_preview(payload)
    summary = str(payload.get("summary") or payload.get("content") or payload.get("message") or "")
    structured_result = payload.get("structured_result")
    if structured_result is None:
        structured_result = {
            key: value
            for key, value in payload.items()
            if key not in {"summary", "content", "message", "download_url", "filename", "generated_at"}
        }

    if downloads and not summary:
        summary = f"{source or 'result'} ready with {len(downloads)} download(s)."
    if not summary and error:
        summary = error

    return {
        "status": status,
        "source": source,
        "summary": summary,
        "structured_result": structured_result,
        "downloads": downloads,
        "preview": preview,
        "metrics": dict(metrics or {}),
        "next_actions": list(next_actions or payload.get("next_actions", []) or []),
        "error": error or None,
        "generated_at": payload.get("generated_at") or datetime.now(timezone.utc).isoformat(),
    }


def _collect_downloads(payload: dict) -> list[dict]:
    downloads = []
    if payload.get("download_url"):
        downloads.append(
            {
                "filename": payload.get("filename", ""),
                "download_url": payload.get("download_url", ""),
                "content_type": payload.get("content_type", ""),
            }
        )
    for item in payload.get("downloads", []) or []:
        if isinstance(item, dict) and item.get("download_url"):
            downloads.append(
                {
                    "filename": item.get("filename", ""),
                    "download_url": item.get("download_url", ""),
                    "content_type": item.get("content_type", ""),
                }
            )
    seen: set[tuple[str, str]] = set()
    unique = []
    for item in downloads:
        key = (item.get("filename", ""), item.get("download_url", ""))
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def _build_preview(payload: dict) -> dict:
    preview: dict[str, object] = {}
    for key in ("topic", "contract_type", "risk_level", "risk_score", "slides_count", "page_name"):
        if key in payload:
            preview[key] = payload[key]
    if "highlights" in payload:
        preview["highlights"] = payload.get("highlights")
    return preview
