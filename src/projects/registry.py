"""项目注册层 —— 管理业务产品、页面和 Skill manifest 绑定。"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class ProjectRegistry:
    def __init__(self, config_dir: str | None = None):
        self.config_dir = Path(config_dir or (Path(__file__).resolve().parent.parent.parent / "config"))
        self._projects: dict[str, dict] = {}
        self._skill_manifests: dict[str, dict] = {}
        self._project_pages: dict[str, list[dict]] = {}
        self._pages_by_route: dict[str, dict] = {}
        self._pages_by_key: dict[tuple[str, str], dict] = {}
        self._loaded = False

    def load(self):
        self._projects.clear()
        self._skill_manifests.clear()
        self._project_pages.clear()
        self._pages_by_route.clear()
        self._pages_by_key.clear()

        projects_dir = self.config_dir / "projects"
        skills_dir = self.config_dir / "skills"

        self._projects["default"] = {
            "id": "default",
            "name": "Default",
            "description": "Implicit default project",
            "skills": [],
            "pages": [],
            "home_route": "",
        }

        for filepath in projects_dir.glob("*.json"):
            data = self._load_json(filepath)
            if not data:
                continue
            project_id = data.get("id") or filepath.stem
            project = {
                "id": project_id,
                "name": data.get("name", project_id),
                "description": data.get("description", ""),
                "skills": data.get("skills", []),
                "pages": [],
                "home_route": data.get("home_route", ""),
            }
            self._projects[project_id] = project
            self._project_pages[project_id] = []

            for page_data in data.get("pages", []):
                page = self._normalize_page(project_id, page_data)
                self._add_page(project_id, page)

        for filepath in skills_dir.glob("*.json"):
            data = self._load_json(filepath)
            if not data:
                continue
            skill_name = data.get("skill_name")
            if not skill_name:
                continue
            manifest = {
                "skill_name": skill_name,
                "project_id": data.get("project_id", "default"),
                "route": data.get("route", f"/{filepath.stem}"),
                "page": data.get("page", f"{filepath.stem}.html"),
                "execution_mode": data.get("execution_mode", "skill_pipeline"),
                "visibility": data.get("visibility", "public"),
                "artifact_kind": data.get("artifact_kind", ""),
            }
            self._skill_manifests[skill_name] = manifest

            project = self._projects.setdefault(
                manifest["project_id"],
                {
                    "id": manifest["project_id"],
                    "name": manifest["project_id"],
                    "description": "",
                    "skills": [],
                },
            )
            if skill_name not in project["skills"]:
                project["skills"].append(skill_name)
            if project["pages"] is None:
                project["pages"] = []

            implicit_page = self._normalize_page(
                manifest["project_id"],
                {
                    "id": filepath.stem,
                    "name": skill_name,
                    "route": manifest["route"],
                    "page": manifest["page"],
                    "skills": [skill_name],
                    "description": data.get("description", manifest["artifact_kind"] or ""),
                    "visibility": manifest["visibility"],
                    "source": "skill_manifest",
                },
            )
            self._add_page(manifest["project_id"], implicit_page, replace=False)

        self._loaded = True
        logger.info(
            "Project registry loaded: %s projects, %s skill manifests, %s pages",
            len(self._projects),
            len(self._skill_manifests),
            sum(len(pages) for pages in self._project_pages.values()),
        )

    def _load_json(self, filepath: Path) -> dict | None:
        try:
            return json.loads(filepath.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Failed to load project config %s: %s", filepath, exc)
            return None

    def _ensure_loaded(self):
        if not self._loaded:
            self.load()

    def get_project(self, project_id: str) -> dict | None:
        self._ensure_loaded()
        project = self._projects.get(project_id)
        return dict(project) if project else None

    def list_projects(self) -> list[dict]:
        self._ensure_loaded()
        return [dict(project) for project in self._projects.values()]

    def list_project_pages(self, project_id: str = "") -> list[dict]:
        self._ensure_loaded()
        if project_id:
            return [dict(page) for page in self._project_pages.get(project_id, [])]
        pages: list[dict] = []
        for project_pages in self._project_pages.values():
            pages.extend(dict(page) for page in project_pages)
        return pages

    def get_project_manifest(self, project_id: str) -> dict | None:
        self._ensure_loaded()
        project = self._projects.get(project_id)
        if project is None:
            return None
        return {
            **dict(project),
            "pages": self.list_project_pages(project_id),
            "skills": list(project.get("skills", [])),
            "skill_manifests": [
                manifest
                for manifest in self.list_skill_manifests()
                if manifest.get("project_id") == project_id
            ],
        }

    def get_page_by_route(self, route: str) -> dict | None:
        self._ensure_loaded()
        page = self._pages_by_route.get(route)
        return dict(page) if page else None

    def get_page(self, project_id: str, page_id: str) -> dict | None:
        self._ensure_loaded()
        page = self._pages_by_key.get((project_id, page_id))
        return dict(page) if page else None

    def get_skill_manifest(self, skill_name: str) -> dict | None:
        self._ensure_loaded()
        manifest = self._skill_manifests.get(skill_name)
        return dict(manifest) if manifest else None

    def list_skill_manifests(self) -> list[dict]:
        self._ensure_loaded()
        return [dict(manifest) for manifest in self._skill_manifests.values()]

    def _normalize_page(self, project_id: str, data: dict) -> dict:
        route = data.get("route", "")
        if route and not route.startswith("/"):
            route = f"/{route}"

        page = {
            "id": data.get("id") or data.get("page", "").replace(".html", "") or data.get("name", "page"),
            "project_id": project_id,
            "name": data.get("name", data.get("id", "Page")),
            "route": route or f"/{data.get('id', 'page')}",
            "page": data.get("page", f"{data.get('id', 'page')}.html"),
            "skills": list(data.get("skills", [])),
            "description": data.get("description", ""),
            "nav_label": data.get("nav_label", data.get("name", data.get("id", "Page"))),
            "icon": data.get("icon", ""),
            "visibility": data.get("visibility", "public"),
            "is_home": bool(data.get("is_home", False)),
            "source": data.get("source", "project_manifest"),
            "billing": self._normalize_billing(data.get("billing")),
            "scenario": self._normalize_scenario(data.get("scenario"), data.get("skills", [])),
        }
        return page

    @staticmethod
    def _normalize_billing(billing: dict | None) -> dict:
        payload = billing if isinstance(billing, dict) else {}
        credits_cost = int(payload.get("credits_cost") or 0)
        return {
            "required": bool(payload.get("required")) and credits_cost > 0,
            "credits_cost": max(credits_cost, 0),
            "reason": payload.get("reason", ""),
        }

    @staticmethod
    def _normalize_scenario(scenario: dict | None, skills: list[str]) -> dict:
        if scenario and scenario.get("steps"):
            steps = []
            for index, step in enumerate(scenario.get("steps", []), start=1):
                step_id = step.get("id") or f"step_{index}"
                steps.append(
                    {
                        "id": step_id,
                        "name": step.get("name", step_id),
                        "skill": step.get("skill", ""),
                        "task_template": step.get("task_template", ""),
                        "run_if": step.get("run_if", ""),
                        "group": step.get("group", ""),
                        "inputs": dict(step.get("inputs", {})),
                        "outputs": dict(step.get("outputs", {})),
                        "fallback_task_template": step.get("fallback_task_template", ""),
                        "continue_on_error": bool(step.get("continue_on_error", False)),
                        "approval_required": bool(step.get("approval_required", False)),
                        "approval_note": step.get("approval_note", ""),
                    }
                )
            return {
                "steps": steps,
                "result_mode": scenario.get("result_mode", "merge"),
            }

        if skills:
            return {
                "steps": [
                    {
                        "id": f"skill_{index + 1}",
                        "name": skill_name,
                        "skill": skill_name,
                        "task_template": "",
                        "run_if": "",
                        "group": "",
                        "inputs": {},
                        "outputs": {},
                        "fallback_task_template": "",
                        "continue_on_error": False,
                        "approval_required": False,
                        "approval_note": "",
                    }
                    for index, skill_name in enumerate(skills)
                ],
                "result_mode": "merge",
            }

        return {"steps": [], "result_mode": "merge"}

    def _add_page(self, project_id: str, page: dict, replace: bool = True):
        project = self._projects.setdefault(
            project_id,
            {
                "id": project_id,
                "name": project_id,
                "description": "",
                "skills": [],
                "pages": [],
                "home_route": "",
            },
        )
        pages = self._project_pages.setdefault(project_id, [])
        existing_index = next((idx for idx, item in enumerate(pages) if item["route"] == page["route"]), None)

        if existing_index is not None:
            if not replace:
                existing = pages[existing_index]
                merged_skills = list(dict.fromkeys(existing.get("skills", []) + page.get("skills", [])))
                existing["skills"] = merged_skills
                self._pages_by_route[existing["route"]] = existing
                return
            pages[existing_index] = page
        else:
            pages.append(page)

        project["pages"] = [p["route"] for p in pages]
        if page.get("is_home"):
            project["home_route"] = page["route"]
        elif not project.get("home_route") and page["route"] == "/":
            project["home_route"] = "/"
        self._pages_by_route[page["route"]] = page
        self._pages_by_key[(project_id, page["id"])] = page


project_registry = ProjectRegistry()
