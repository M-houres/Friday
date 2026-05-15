"""Managed ops configuration stored as repo-local JSON files."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from src.config import settings
from src.models.tiering import ComplexityClassifier

DEFAULT_SYSTEM_SETTINGS: dict[str, Any] = {
    "kind": "system_config",
    "version": 1,
    "site_name": "星期五",
    "brand_subtitle": "AI 产品运营控制台",
    "timezone": "Asia/Shanghai",
    "language": "zh-CN",
    "support_email": "",
    "default_project_id": "default",
    "billing_currency": "CNY",
    "ops_notice": "",
    "feature_flags": {
        "billing_enabled": True,
        "growth_enabled": True,
        "support_enabled": True,
        "approvals_enabled": True,
    },
    "metadata": {},
}

DEFAULT_MODEL_STRATEGY: dict[str, Any] = {
    "kind": "model_strategy",
    "version": 1,
    "default_model": settings.default_model,
    "fast_model": settings.default_fast_model,
    "complexity_routing_enabled": True,
    "complexity_overrides": {
        "trivial": "deepseek-chat",
        "simple": "deepseek-chat",
        "moderate": settings.default_model,
        "complex": "gpt-4o",
        "expert": "deepseek-reasoner",
    },
    "fallback_map": {
        "gpt-4o": "gpt-4o-mini",
        "gpt-4o-mini": "deepseek-chat",
        "claude-sonnet-4-20250514": "claude-haiku-4-5",
        "claude-opus-4-1": "claude-sonnet-4-20250514",
        "deepseek-reasoner": "deepseek-chat",
    },
    "page_strategies": [],
    "metadata": {},
}


def _merge_dict(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_dict(merged[key], value)
        else:
            merged[key] = value
    return merged


class ManagedConfigStore:
    def __init__(self, root_dir: str | Path | None = None):
        repo_root = Path(__file__).resolve().parents[2]
        self.root_dir = Path(root_dir or (repo_root / "config" / "managed"))
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        mapping = {
            "system_config": "system_settings.json",
            "model_strategy": "model_strategy.json",
        }
        filename = mapping.get(key)
        if not filename:
            raise ValueError(f"Unsupported managed config key: {key}")
        return self.root_dir / filename

    def _defaults(self, key: str) -> dict[str, Any]:
        if key == "system_config":
            return deepcopy(DEFAULT_SYSTEM_SETTINGS)
        if key == "model_strategy":
            return deepcopy(DEFAULT_MODEL_STRATEGY)
        raise ValueError(f"Unsupported managed config key: {key}")

    def _read(self, key: str) -> dict[str, Any]:
        path = self._path(key)
        default_value = self._defaults(key)
        if not path.exists():
            self._write(key, default_value)
            return default_value
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            self._write(key, default_value)
            return default_value
        if not isinstance(payload, dict):
            self._write(key, default_value)
            return default_value
        merged = _merge_dict(default_value, payload)
        if merged != payload:
            self._write(key, merged)
        return merged

    def _write(self, key: str, payload: dict[str, Any]) -> dict[str, Any]:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return payload

    def get_system_settings(self) -> dict[str, Any]:
        return self._read("system_config")

    def update_system_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        current = self.get_system_settings()
        updated = _merge_dict(current, payload or {})
        return self._write("system_config", updated)

    def get_model_strategy(self) -> dict[str, Any]:
        return self._read("model_strategy")

    def update_model_strategy(self, payload: dict[str, Any]) -> dict[str, Any]:
        current = self.get_model_strategy()
        updated = _merge_dict(current, payload or {})
        updated["page_strategies"] = [
            {
                "project_id": str(item.get("project_id") or ""),
                "page_id": str(item.get("page_id") or ""),
                "model": str(item.get("model") or ""),
                "fast_model": str(item.get("fast_model") or ""),
                "notes": str(item.get("notes") or ""),
            }
            for item in list(updated.get("page_strategies") or [])
            if isinstance(item, dict)
        ]
        updated["fallback_map"] = dict(updated.get("fallback_map") or {})
        updated["complexity_overrides"] = dict(updated.get("complexity_overrides") or {})
        return self._write("model_strategy", updated)

    def get_managed_config(self, key: str) -> dict[str, Any]:
        if key == "system_config":
            return self.get_system_settings()
        if key == "model_strategy":
            return self.get_model_strategy()
        raise ValueError(f"Unsupported managed config key: {key}")

    def update_managed_config(self, key: str, payload: dict[str, Any]) -> dict[str, Any]:
        if key == "system_config":
            return self.update_system_settings(payload)
        if key == "model_strategy":
            return self.update_model_strategy(payload)
        raise ValueError(f"Unsupported managed config key: {key}")

    def resolve_model(self, task: str, project_id: str = "", page_id: str = "", preferred: str = "") -> str:
        if preferred:
            return preferred
        strategy = self.get_model_strategy()
        page_strategy = self._match_page_strategy(strategy, project_id, page_id)
        if page_strategy and page_strategy.get("model"):
            return str(page_strategy["model"])
        if bool(strategy.get("complexity_routing_enabled")):
            complexity = ComplexityClassifier.classify(task).name.lower()
            override = str((strategy.get("complexity_overrides") or {}).get(complexity) or "")
            if override:
                return override
        return str(strategy.get("default_model") or settings.default_model)

    def resolve_fast_model(self, project_id: str = "", page_id: str = "", preferred: str = "") -> str:
        if preferred:
            return preferred
        strategy = self.get_model_strategy()
        page_strategy = self._match_page_strategy(strategy, project_id, page_id)
        if page_strategy and page_strategy.get("fast_model"):
            return str(page_strategy["fast_model"])
        return str(strategy.get("fast_model") or strategy.get("default_model") or settings.default_fast_model)

    def resolve_fallback(self, model: str) -> str | None:
        strategy = self.get_model_strategy()
        fallback = str((strategy.get("fallback_map") or {}).get(model) or "")
        return fallback or None

    @staticmethod
    def _match_page_strategy(strategy: dict[str, Any], project_id: str, page_id: str) -> dict[str, Any] | None:
        page_strategies = list(strategy.get("page_strategies") or [])
        if project_id and page_id:
            for item in page_strategies:
                if item.get("project_id") == project_id and item.get("page_id") == page_id:
                    return item
        if project_id:
            for item in page_strategies:
                if item.get("project_id") == project_id and not item.get("page_id"):
                    return item
        return None


managed_config_store = ManagedConfigStore()
