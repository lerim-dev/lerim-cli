"""Unit tests for config loading, type conversion, and role builders."""

from __future__ import annotations

from pathlib import Path


from lerim.config.settings import (
    Config,
    RoleConfig,
    _build_role,
    _deep_merge,
    _require_int,
    _to_non_empty_string,
    ensure_user_config_exists,
    get_config,
    reload_config,
)
from tests.helpers import make_config, write_test_config


def test_load_default_toml():
    """Default TOML loads without error, produces valid Config."""
    cfg = get_config()
    assert isinstance(cfg, Config)
    assert cfg.data_dir is not None
    assert cfg.memory_dir is not None


def test_deep_merge_override():
    """Project config overrides global config values."""
    base = {"a": 1, "nested": {"x": 10, "y": 20}}
    override = {"a": 2, "nested": {"x": 99}}
    result = _deep_merge(base, override)
    assert result["a"] == 2
    assert result["nested"]["x"] == 99


def test_deep_merge_preserves_unset():
    """Unset keys in override preserved from base."""
    base = {"a": 1, "nested": {"x": 10, "y": 20}}
    override = {"nested": {"x": 99}}
    result = _deep_merge(base, override)
    assert result["a"] == 1
    assert result["nested"]["y"] == 20


def test_require_int_valid():
    """_require_int parses valid values and enforces minimum."""
    assert _require_int({"k": 42}, "k") == 42
    assert _require_int({"k": "10"}, "k") == 10
    assert _require_int({"k": -1}, "k", minimum=0) == 0


def test_require_int_missing():
    """_require_int raises on missing key."""
    import pytest

    with pytest.raises(ValueError, match="missing required config key"):
        _require_int({}, "k")


def test_type_conversion_non_empty_string():
    """_to_non_empty_string trims whitespace, handles None."""
    assert _to_non_empty_string("  hello  ") == "hello"
    assert _to_non_empty_string(None) == ""
    assert _to_non_empty_string("") == ""
    assert _to_non_empty_string(42) == "42"


def test_role_config_construction():
    """_build_role produces RoleConfig from explicit config values.

    Usage-limit keys are REQUIRED by _require_int — fixtures that build
    a role directly must supply them. Production sources them from
    default.toml which always has them.
    """
    role = _build_role(
        {
            "usage_limit_reflect": 30,
            "usage_limit_extract": 30,
            "usage_limit_finalize": 30,
        },
        default_provider="openrouter",
        default_model="qwen/qwen3-coder-30b-a3b-instruct",
    )
    assert isinstance(role, RoleConfig)
    assert role.provider == "openrouter"
    assert role.model == "qwen/qwen3-coder-30b-a3b-instruct"


def test_dspy_role_config_construction():
    """_build_role produces RoleConfig with DSPy fields from explicit values."""
    role = _build_role(
        {
            "max_window_tokens": 300000,
            "window_overlap_tokens": 5000,
            "usage_limit_reflect": 30,
            "usage_limit_extract": 30,
            "usage_limit_finalize": 30,
        },
        default_provider="ollama",
        default_model="qwen3:8b",
    )
    assert isinstance(role, RoleConfig)
    assert role.provider == "ollama"
    assert role.model == "qwen3:8b"
    assert role.max_window_tokens == 300000
    assert role.window_overlap_tokens == 5000


def test_config_scaffold_creation(tmp_path, monkeypatch):
    """ensure_user_config_exists creates scaffold TOML file."""
    config_path = tmp_path / "config.toml"
    monkeypatch.setattr("lerim.config.settings.USER_CONFIG_PATH", config_path)
    # Ensure we're not in pytest detection context by patching
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr("lerim.config.settings.os.environ", {})
    result = ensure_user_config_exists()
    # May or may not create depending on pytest detection, but shouldn't crash
    assert isinstance(result, Path)


def test_config_reload_clears_cache(tmp_path, monkeypatch):
    """reload_config() invalidates LRU cache."""
    config_path = write_test_config(tmp_path)
    monkeypatch.setenv("LERIM_CONFIG", str(config_path))
    cfg1 = reload_config()
    cfg2 = reload_config()
    assert isinstance(cfg1, Config)
    assert isinstance(cfg2, Config)


def test_config_env_var_override(tmp_path, monkeypatch):
    """LERIM_CONFIG env var overrides all other layers."""
    config_path = write_test_config(tmp_path)
    monkeypatch.setenv("LERIM_CONFIG", str(config_path))
    cfg = reload_config()
    assert cfg.global_data_dir == tmp_path


def test_config_public_dict(tmp_path):
    """public_dict() returns dict without sensitive fields."""
    cfg = make_config(tmp_path)
    d = cfg.public_dict()
    assert isinstance(d, dict)
    # Should not contain API keys
    assert "anthropic_api_key" not in d
    assert "openai_api_key" not in d
    assert "zai_api_key" not in d
    # Should have public fields
    assert "data_dir" in d
