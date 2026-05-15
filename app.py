"""
星期五 应用入口 —— 垂直 AI 应用装配底座

用法:
  python app.py                         # 启动全部已装配应用
  python app.py --skill=ppt            # 仅加载指定 Skill
  python scripts/new_app.py ...        # 生成新的垂直应用骨架

面板: http://localhost:8000/panel
API:   http://localhost:8000/docs
"""

import sys
import importlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# 导入框架
from src.main import app
from src.projects.registry import project_registry
from src.tools.skill import skill_registry

# 挂载静态文件
from fastapi.staticfiles import StaticFiles

static_dir = Path(__file__).resolve().parent / "static"
static_dir.mkdir(parents=True, exist_ok=True)
(static_dir / "output").mkdir(parents=True, exist_ok=True)

app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


from fastapi.responses import FileResponse

def _register_static_pages():
    registered_routes: set[str] = set()

    for page in project_registry.list_project_pages():
        page_name = page.get("page", "")
        route_path = page.get("route", "")
        if not page_name or not route_path:
            continue
        page_path = static_dir / page_name
        if not page_path.exists() or route_path in registered_routes:
            continue

        async def page_handler(page_path=page_path):
            return FileResponse(str(page_path))

        app.add_api_route(route_path, page_handler, methods=["GET"])
        registered_routes.add(route_path)

    for page_file in static_dir.glob("*.html"):
        route_path = f"/{page_file.stem}"
        if route_path in registered_routes:
            continue

        async def page_handler(page_path=page_file):
            return FileResponse(str(page_path))

        app.add_api_route(route_path, page_handler, methods=["GET"])


# ── 注册 Skill ──

def _discover_skill_modules(skill_filter: str = "") -> list[str]:
    modules: list[str] = []
    skills_dir = Path(__file__).resolve().parent / "skills"
    for py_file in skills_dir.glob("*.py"):
        if py_file.name.startswith("_"):
            continue
        module_name = py_file.stem
        normalized = module_name.replace("_skill", "")
        if skill_filter and skill_filter not in ("all", normalized, module_name):
            continue
        modules.append(f"skills.{module_name}")
    return sorted(modules)


def _load_skills():
    """根据命令行参数或环境变量决定加载哪些 Skill"""
    import os
    skill_filter = os.environ.get("FRIDAY_SKILL", "").lower()

    # 命令行参数覆盖环境变量
    args = [a for a in sys.argv if a.startswith("--skill=")]
    if args:
        skill_filter = args[0].split("=", 1)[1].lower()

    for module_name in _discover_skill_modules(skill_filter):
        importlib.import_module(module_name)


_load_skills()
project_registry.load()
skill_registry.apply_manifest_metadata()
_register_static_pages()

if __name__ == "__main__":
    import uvicorn
    from src.config import settings

    uvicorn.run(
        "app:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        reload=False,
    )
