"""Generate vertical app boilerplate from local templates."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


_TOKEN_PATTERN = re.compile(r"__([A-Z0-9_]+)__")


@dataclass(slots=True)
class ScaffoldOptions:
    app_id: str
    app_name: str
    trigger: str
    description: str = ""
    route: str = ""
    page: str = ""
    icon: str = "🧩"
    project_id: str = "default"
    project_name: str = ""
    project_description: str = ""
    artifact_kind: str = ""
    visibility: str = "public"
    create_project_config: bool = False
    overwrite: bool = False

    @property
    def module_id(self) -> str:
        return _normalize_app_id(self.app_id)

    @property
    def page_slug(self) -> str:
        return self.module_id.replace("_", "-")

    @property
    def route_path(self) -> str:
        if self.route:
            route = self.route.strip()
            return route if route.startswith("/") else f"/{route}"
        return f"/{self.page_slug}"

    @property
    def page_name(self) -> str:
        return self.page or f"{self.page_slug}.html"

    @property
    def skill_description(self) -> str:
        return self.description or f"{self.app_name} 垂直业务 Skill 骨架"

    @property
    def resolved_project_name(self) -> str:
        return self.project_name or self.app_name

    @property
    def resolved_project_description(self) -> str:
        return self.project_description or self.skill_description


class VerticalAppScaffoldGenerator:
    """Create app skill/page/manifest files with minimal repeated edits."""

    def __init__(self, repo_root: str | Path | None = None, template_root: str | Path | None = None):
        self.repo_root = Path(repo_root or Path(__file__).resolve().parents[2])
        self.template_root = Path(template_root or (self.repo_root / "templates" / "vertical_app"))

    def generate(self, options: ScaffoldOptions) -> dict[str, str]:
        context = self._build_context(options)

        targets = {
            self.template_root / "skill.py.template": self.repo_root / "skills" / f"{options.module_id}_skill.py",
            self.template_root / "page.html.template": self.repo_root / "static" / options.page_name,
            self.template_root / "skill_manifest.json.template": self.repo_root / "config" / "skills" / f"{options.module_id}.json",
        }

        if options.create_project_config:
            targets[self.template_root / "project.json.template"] = (
                self.repo_root / "config" / "projects" / f"{options.project_id}.json"
            )

        self._assert_writable(targets.values(), overwrite=options.overwrite)

        written: dict[str, str] = {}
        for template_path, target_path in targets.items():
            target_path.parent.mkdir(parents=True, exist_ok=True)
            rendered = self._render_template(template_path, context)
            target_path.write_text(rendered, encoding="utf-8")
            written[str(template_path.relative_to(self.template_root))] = str(target_path)

        return written

    def _build_context(self, options: ScaffoldOptions) -> dict[str, str]:
        return {
            "APP_ID": options.module_id,
            "APP_NAME": options.app_name,
            "APP_TRIGGER": options.trigger,
            "APP_DESCRIPTION": options.skill_description,
            "APP_ICON": options.icon,
            "APP_ROUTE": options.route_path,
            "APP_PAGE": options.page_name,
            "APP_SLUG": options.page_slug,
            "APP_CLASS_NAME": _pascal_case(options.module_id) + "Skill",
            "PROJECT_ID": options.project_id,
            "PROJECT_NAME": options.resolved_project_name,
            "PROJECT_DESCRIPTION": options.resolved_project_description,
            "ARTIFACT_KIND": options.artifact_kind,
            "VISIBILITY": options.visibility,
        }

    def _render_template(self, template_path: Path, context: dict[str, str]) -> str:
        template = template_path.read_text(encoding="utf-8")

        def replacer(match: re.Match[str]) -> str:
            key = match.group(1)
            return context.get(key, match.group(0))

        return _TOKEN_PATTERN.sub(replacer, template)

    @staticmethod
    def _assert_writable(paths: list[Path] | tuple[Path, ...] | object, overwrite: bool):
        for path in paths:
            if Path(path).exists() and not overwrite:
                raise FileExistsError(f"Refusing to overwrite existing file: {path}")


def _normalize_app_id(value: str) -> str:
    app_id = value.strip().lower().replace("-", "_")
    if not re.fullmatch(r"[a-z][a-z0-9_]*", app_id):
        raise ValueError("app_id must match [a-z][a-z0-9_-]* and start with a letter")
    return app_id


def _pascal_case(value: str) -> str:
    return "".join(part[:1].upper() + part[1:] for part in value.split("_") if part)
