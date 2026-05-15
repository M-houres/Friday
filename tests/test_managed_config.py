from pathlib import Path


def test_managed_config_store_reads_and_updates_files(tmp_path: Path):
    from src.productization.managed_config import ManagedConfigStore

    store = ManagedConfigStore(tmp_path)
    system_settings = store.get_system_settings()
    assert system_settings["site_name"] == "星期五"

    updated = store.update_system_settings({"site_name": "Friday Ops", "feature_flags": {"growth_enabled": False}})
    assert updated["site_name"] == "Friday Ops"
    assert updated["feature_flags"]["growth_enabled"] is False

    reread = store.get_system_settings()
    assert reread["site_name"] == "Friday Ops"


def test_managed_config_store_resolves_models_from_page_and_complexity(tmp_path: Path):
    from src.productization.managed_config import ManagedConfigStore

    store = ManagedConfigStore(tmp_path)
    store.update_model_strategy(
        {
            "default_model": "deepseek-chat",
            "fast_model": "gpt-4o-mini",
            "complexity_routing_enabled": True,
            "complexity_overrides": {"simple": "deepseek-chat", "complex": "gpt-4o"},
            "page_strategies": [
                {"project_id": "default", "page_id": "vip", "model": "claude-sonnet-4-20250514", "fast_model": "claude-haiku-4-5"}
            ],
        }
    )

    page_model = store.resolve_model("hello", project_id="default", page_id="vip")
    default_model = store.resolve_model("请帮我设计一个复杂架构方案，需要重构和性能分析")
    fast_model = store.resolve_fast_model(project_id="default", page_id="vip")

    assert page_model == "claude-sonnet-4-20250514"
    assert default_model == "gpt-4o"
    assert fast_model == "claude-haiku-4-5"


def test_managed_config_store_resolves_fallback_map(tmp_path: Path):
    from src.productization.managed_config import ManagedConfigStore

    store = ManagedConfigStore(tmp_path)
    store.update_model_strategy({"fallback_map": {"gpt-4o": "gpt-4o-mini"}})
    assert store.resolve_fallback("gpt-4o") == "gpt-4o-mini"
    assert store.resolve_fallback("deepseek-chat") is None
