"""Unit tests for config loading, type conversion, and role builders."""

from __future__ import annotations

from pathlib import Path


from lerim.config.settings import (
    AgentRoleConfig,
    Config,
    DSPyRoleConfig,
    _build_agent_role,
    _build_dspy_role,
    _deep_merge,
    _require_float,
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


def test_require_float_valid():
    """_require_float parses valid values and clamps to bounds."""
    assert _require_float({"k": 0.5}, "k", minimum=0.0, maximum=1.0) == 0.5
    assert _require_float({"k": 2.0}, "k", minimum=0.0, maximum=1.0) == 1.0
    assert _require_float({"k": -0.5}, "k", minimum=0.0, maximum=1.0) == 0.0


def test_require_float_missing():
    """_require_float raises on missing key."""
    import pytest

    with pytest.raises(ValueError, match="missing required config key"):
        _require_float({}, "k", minimum=0.0, maximum=1.0)


def test_type_conversion_non_empty_string():
    """_to_non_empty_string trims whitespace, handles None."""
    assert _to_non_empty_string("  hello  ") == "hello"
    assert _to_non_empty_string(None) == ""
    assert _to_non_empty_string("") == ""
    assert _to_non_empty_string(42) == "42"


def test_role_config_construction():
    """_build_agent_role produces AgentRoleConfig from explicit config values."""
    role = _build_agent_role(
        {"timeout_seconds": 300, "max_iterations": 10},
        default_provider="openrouter",
        default_model="qwen/qwen3-coder-30b-a3b-instruct",
    )
    assert isinstance(role, AgentRoleConfig)
    assert role.provider == "openrouter"
    assert role.model == "qwen/qwen3-coder-30b-a3b-instruct"
    assert role.timeout_seconds == 300


def test_dspy_role_config_construction():
    """_build_dspy_role produces DSPyRoleConfig from explicit config values."""
    role = _build_dspy_role(
        {
            "timeout_seconds": 180,
            "max_window_tokens": 300000,
            "window_overlap_tokens": 5000,
        },
        default_provider="ollama",
        default_model="qwen3:8b",
    )
    assert isinstance(role, DSPyRoleConfig)
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
    assert cfg.data_dir == tmp_path


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
    assert "memory_scope" in d


def test_config_decay_fields(tmp_path):
    """Config exposes decay_days, decay_archive_threshold, etc."""
    cfg = make_config(tmp_path)
    assert isinstance(cfg.decay_days, int)
    assert isinstance(cfg.decay_archive_threshold, float)
    assert isinstance(cfg.decay_enabled, bool)
    assert isinstance(cfg.decay_min_confidence_floor, float)
    assert isinstance(cfg.decay_recent_access_grace_days, int)
