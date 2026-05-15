from pathlib import Path


def test_env_file_does_not_embed_real_deepseek_secret():
    env_source = Path("config/.env").read_text(encoding="utf-8")

    assert "sk-cda9a75f2f844833b0ca250cdc24f29c" not in env_source
    assert "DEEPSEEK_API_KEY=sk-your-deepseek-key" in env_source


def test_subprocess_sandbox_uses_exec_not_shell():
    source = Path("src/tools/sandbox.py").read_text(encoding="utf-8")

    assert "create_subprocess_shell" not in source
    assert "create_subprocess_exec" in source


def test_isolated_sandbox_uses_exec_not_shell():
    source = Path("src/tools/isolated_sandbox.py").read_text(encoding="utf-8")

    assert "create_subprocess_shell" not in source
    assert "create_subprocess_exec" in source
    assert "sys.executable" in source


def test_document_parse_rejects_paths_outside_allowed_roots(tmp_path):
    import pytest

    from src.tools.generic_tools import document_parse

    outside_dir = tmp_path.parent / "outside-docs"
    outside_dir.mkdir(parents=True, exist_ok=True)
    outside_file = outside_dir / "secret.txt"
    outside_file.write_text("secret", encoding="utf-8")

    with pytest.raises(ValueError, match="DOCUMENT_PATH_NOT_ALLOWED"):
        document_parse(path=str(outside_file))
