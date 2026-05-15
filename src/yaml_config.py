"""YAML 驱动配置 —— Agent/Skill/Workflow 定义与代码分离"""

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class YAMLConfig:
    """YAML 配置加载器 —— 从 YAML 文件加载 Agent/Skill/Workflow 定义"""

    def __init__(self, config_dir: str = "config"):
        self.config_dir = Path(config_dir)
        self._agents: dict[str, dict] = {}
        self._skills: dict[str, dict] = {}
        self._workflows: dict[str, dict] = {}
        self._loaded = False

    def load(self):
        """加载所有 YAML 配置"""
        import json
        # 用 JSON 格式作为 YAML 的轻量替代（Python 不需要额外依赖）
        for ext in ("*.json", "*.yaml", "*.yml"):
            for filepath in self.config_dir.glob(f"**/{ext}"):
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        if ext == "*.json":
                            data = json.load(f)
                        else:
                            try:
                                import yaml
                                data = yaml.safe_load(f)
                            except ImportError:
                                logger.warning("PyYAML not installed, skipping .yaml files")
                                continue

                    self._register(filepath.stem, data, filepath.suffix)
                except Exception as e:
                    logger.warning(f"Failed to load config {filepath}: {e}")

        self._loaded = True
        logger.info(f"YAML config loaded: {len(self._agents)} agents, {len(self._skills)} skills, {len(self._workflows)} workflows")

    def _register(self, name: str, data: dict, suffix: str):
        kind = data.get("kind", data.get("type", ""))
        if kind == "agent" or "system_prompt" in data:
            self._agents[name] = self._normalize_agent(data)
        elif kind == "skill" or "tools" in data:
            self._skills[name] = data
        elif kind == "workflow" or "steps" in data:
            self._workflows[name] = data

    def _normalize_agent(self, data: dict) -> dict:
        return {
            "name": data.get("name", "unnamed"),
            "system_prompt": data.get("system_prompt", ""),
            "model": data.get("model", "deepseek-chat"),
            "strategy": data.get("strategy", "react"),
            "tools": data.get("tools", []),
            "temperature": data.get("temperature", 0.7),
            "max_tokens": data.get("max_tokens", 4096),
        }

    def get_agent(self, name: str) -> dict | None:
        if not self._loaded:
            self.load()
        return self._agents.get(name)

    def get_skill(self, name: str) -> dict | None:
        if not self._loaded:
            self.load()
        return self._skills.get(name)

    def get_workflow(self, name: str) -> dict | None:
        if not self._loaded:
            self.load()
        return self._workflows.get(name)

    def list_all(self) -> dict:
        if not self._loaded:
            self.load()
        return {
            "agents": list(self._agents.keys()),
            "skills": list(self._skills.keys()),
            "workflows": list(self._workflows.keys()),
        }

    @staticmethod
    def create_agent_config(name: str, system_prompt: str, model: str = "deepseek-chat",
                            tools: list[str] | None = None) -> dict:
        """创建 Agent 配置模板"""
        return {
            "kind": "agent",
            "name": name,
            "system_prompt": system_prompt,
            "model": model,
            "strategy": "react",
            "tools": tools or [],
            "temperature": 0.7,
            "max_tokens": 4096,
        }

    @staticmethod
    def create_skill_config(name: str, description: str, trigger: str,
                            tools: list[dict], workflow: list[dict]) -> dict:
        """创建 Skill 配置模板"""
        return {
            "kind": "skill",
            "name": name,
            "description": description,
            "trigger": trigger,
            "version": "1.0.0",
            "tools": tools,
            "workflow": workflow,
        }

    @staticmethod
    def create_workflow_config(name: str, steps: list[dict]) -> dict:
        """创建工作流配置模板"""
        return {
            "kind": "workflow",
            "name": name,
            "steps": steps,
        }


yaml_config = YAMLConfig()
