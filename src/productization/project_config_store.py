"""Managed project/page assembly configuration persisted to config/projects."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from src.projects.registry import project_registry


def _normalize_project_payload(project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    pages = []
    for page in list(payload.get("pages") or []):
        if not isinstance(page, dict):
            continue
        route = str(page.get("route") or "").strip()
        if route and not route.startswith("/"):
            route = f"/{route}"
        page_id = str(page.get("id") or page.get("page") or page.get("name") or "page").replace(".html", "").strip()
        skills = [str(item).strip() for item in list(page.get("skills") or []) if str(item).strip()]
        steps = []
        scenario = page.get("scenario") if isinstance(page.get("scenario"), dict) else {}
        for index, step in enumerate(list(scenario.get("steps") or []), start=1):
            if not isinstance(step, dict):
                continue
            step_id = str(step.get("id") or f"step_{index}").strip()
            steps.append(
                {
                    "id": step_id,
                    "name": str(step.get("name") or step_id).strip(),
                    "skill": str(step.get("skill") or "").strip(),
                    "task_template": str(step.get("task_template") or ""),
                    "run_if": str(step.get("run_if") or ""),
                    "group": str(step.get("group") or ""),
                    "inputs": dict(step.get("inputs") or {}),
                    "outputs": dict(step.get("outputs") or {}),
                    "fallback_task_template": str(step.get("fallback_task_template") or ""),
                    "continue_on_error": bool(step.get("continue_on_error", False)),
                    "approval_required": bool(step.get("approval_required", False)),
                    "approval_note": str(step.get("approval_note") or ""),
                }
            )
        normalized_page = {
            "id": page_id,
            "name": str(page.get("name") or page_id).strip(),
            "route": route or f"/{page_id}",
            "page": str(page.get("page") or f"{page_id}.html").strip(),
            "skills": skills,
            "description": str(page.get("description") or ""),
            "nav_label": str(page.get("nav_label") or page.get("name") or page_id),
            "icon": str(page.get("icon") or ""),
            "visibility": str(page.get("visibility") or "public"),
            "is_home": bool(page.get("is_home", False)),
            "billing": {
                "required": bool(((page.get("billing") or {}).get("required"))),
                "credits_cost": max(int(((page.get("billing") or {}).get("credits_cost") or 0)), 0),
                "reason": str(((page.get("billing") or {}).get("reason") or "")),
            },
        }
        if steps:
            normalized_page["scenario"] = {
                "steps": steps,
                "result_mode": str(scenario.get("result_mode") or "merge"),
            }
        pages.append(normalized_page)

    project_skills = [str(item).strip() for item in list(payload.get("skills") or []) if str(item).strip()]
    page_skills = [skill for page in pages for skill in list(page.get("skills") or [])]
    merged_skills = list(dict.fromkeys(project_skills + page_skills))
    home_route = str(payload.get("home_route") or "").strip()
    page_routes = {str(page.get("route") or "") for page in pages if str(page.get("route") or "")}
    if not home_route or home_route not in page_routes:
        home_page = next((page for page in pages if page.get("is_home")), None)
        if home_page:
            home_route = str(home_page.get("route") or "")
        elif "/" in page_routes:
            home_route = "/"
        elif pages:
            home_route = str(pages[0].get("route") or "")
    return {
        "kind": "project",
        "id": project_id,
        "name": str(payload.get("name") or project_id).strip(),
        "description": str(payload.get("description") or ""),
        "home_route": home_route,
        "skills": merged_skills,
        "pages": pages,
    }


class ProjectConfigStore:
    def __init__(self, config_dir: str | Path | None = None):
        repo_root = Path(__file__).resolve().parents[2]
        self.config_dir = Path(config_dir or (repo_root / "config" / "projects"))
        self.config_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, project_id: str) -> Path:
        return self.config_dir / f"{project_id}.json"

    def _read(self, project_id: str) -> dict[str, Any] | None:
        path = self._path(project_id)
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return None
        return _normalize_project_payload(project_id, payload)

    def _write(self, project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = _normalize_project_payload(project_id, payload)
        self._path(project_id).write_text(
            json.dumps(normalized, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        project_registry.load()
        return deepcopy(normalized)

    def list_projects(self) -> list[dict[str, Any]]:
        project_registry.load()
        return [deepcopy(project_registry.get_project_manifest(project["id"])) for project in project_registry.list_projects() if project["id"] != "default" or self._path("default").exists()]

    def get_project(self, project_id: str) -> dict[str, Any] | None:
        payload = self._read(project_id)
        if payload is not None:
            return payload
        manifest = project_registry.get_project_manifest(project_id)
        if manifest is None:
            return None
        return _normalize_project_payload(project_id, manifest)

    def save_project(self, payload: dict[str, Any]) -> dict[str, Any]:
        project_id = str(payload.get("id") or "").strip()
        if not project_id:
            raise ValueError("PROJECT_ID_REQUIRED")
        existing = self.get_project(project_id)
        merged = deepcopy(existing or {})
        merged.update(payload)
        if (
            existing is not None
            and (
                "pages" not in payload
                or payload.get("pages") is None
            )
        ):
            merged["pages"] = list(existing.get("pages") or [])
        return self._write(project_id, merged)

    def delete_project(self, project_id: str) -> bool:
        path = self._path(project_id)
        if not path.exists():
            return False
        path.unlink()
        project_registry.load()
        return True

    def upsert_page(self, project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        project = self.get_project(project_id)
        if project is None:
            raise ValueError("PROJECT_NOT_FOUND")
        page_id = str(payload.get("id") or payload.get("page") or "").replace(".html", "").strip()
        if not page_id:
            raise ValueError("PAGE_ID_REQUIRED")
        pages = list(project.get("pages") or [])
        updated = False
        for index, item in enumerate(pages):
            if str(item.get("id") or "") == page_id:
                merged = dict(item)
                merged.update(payload)
                pages[index] = merged
                updated = True
                break
        if not updated:
            pages.append(dict(payload, id=page_id))
        project["pages"] = pages
        if payload.get("is_home"):
            project["home_route"] = str(payload.get("route") or f"/{page_id}")
        elif not str(project.get("home_route") or "").strip():
            project["home_route"] = self._resolve_home_route(pages)
        return self._write(project_id, project)

    def delete_page(self, project_id: str, page_id: str) -> dict[str, Any] | None:
        project = self.get_project(project_id)
        if project is None:
            return None
        pages = [page for page in list(project.get("pages") or []) if str(page.get("id") or "") != page_id]
        if len(pages) == len(list(project.get("pages") or [])):
            return None
        deleted_page = next((page for page in list(project.get("pages") or []) if str(page.get("id") or "") == page_id), None)
        project["pages"] = pages
        deleted_route = str((deleted_page or {}).get("route") or "")
        if deleted_page and (
            bool(deleted_page.get("is_home"))
            or str(project.get("home_route") or "") == deleted_route
            or str(project.get("home_route") or "") == f"/{page_id}"
        ):
            project["home_route"] = self._resolve_home_route(pages)
        return self._write(project_id, project)

    @staticmethod
    def _resolve_home_route(pages: list[dict[str, Any]]) -> str:
        home_page = next((page for page in pages if page.get("is_home")), None)
        if home_page is not None:
            return str(home_page.get("route") or "")
        root_page = next((page for page in pages if str(page.get("route") or "") == "/"), None)
        if root_page is not None:
            return "/"
        first_page = pages[0] if pages else {}
        return str(first_page.get("route") or "")


project_config_store = ProjectConfigStore()
