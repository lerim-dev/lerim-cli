"""Unit tests for settings.py coverage gaps not covered by test_config.py.

Tests: load_toml_file, _expand, _to_fallback_models, _to_string_tuple,
_parse_string_table, _toml_value, _toml_write_dict, save_config_patch,
layer precedence, port clamping.
"""

from __future__ import annotations

from pathlib import Path

from lerim.config.settings import (
    _deep_merge,
    _expand,
    _to_fallback_models,
    _to_string_tuple,
    _parse_string_table,
    _toml_value,
    _toml_write_dict,
    _build_dspy_role,
    _build_llm_role,
    load_toml_file,
    save_config_patch,
    reload_config,
)


# ---------------------------------------------------------------------------
# load_toml_file
# ---------------------------------------------------------------------------


def test_load_toml_file_valid(tmp_path):
    """load_toml_file returns parsed dict from valid TOML file."""
    toml_file = tmp_path / "test.toml"
    toml_file.write_text('[section]\nkey = "value"\n', encoding="utf-8")
    result = load_toml_file(toml_file)
    assert result == {"section": {"key": "value"}}


def test_load_toml_file_missing():
    """load_toml_file returns empty dict for non-existent path."""
    assert load_toml_file(Path("/nonexistent/path.toml")) == {}


def test_load_toml_file_none():
    """load_toml_file returns empty dict when path is None."""
    assert load_toml_file(None) == {}


def test_load_toml_file_invalid(tmp_path):
    """load_toml_file returns empty dict for malformed TOML."""
    bad = tmp_path / "bad.toml"
    bad.write_text("this is not valid toml [[[", encoding="utf-8")
    assert load_toml_file(bad) == {}


# ---------------------------------------------------------------------------
# _expand
# ---------------------------------------------------------------------------


def test_expand_with_valid_path(tmp_path):
    """_expand resolves a provided path string."""
    result = _expand(str(tmp_path), default=Path("/default"))
    assert result == tmp_path


def test_expand_with_none():
    """_expand returns default when value is None."""
    default = Path("/fallback")
    assert _expand(None, default) == default


def test_expand_with_empty_string():
    """_expand returns default when value is empty string."""
    default = Path("/fallback")
    assert _expand("", default) == default


def test_expand_with_tilde():
    """_expand expands ~ to home directory."""
    result = _expand("~/test", default=Path("/default"))
    assert result == Path.home() / "test"


# ---------------------------------------------------------------------------
# _to_fallback_models
# ---------------------------------------------------------------------------


def test_fallback_models_from_list():
    """_to_fallback_models parses a list of model strings."""
    result = _to_fallback_models(["model-a", "model-b"])
    assert result == ("model-a", "model-b")


def test_fallback_models_from_csv_string():
    """_to_fallback_models parses comma-separated string."""
    result = _to_fallback_models("model-a, model-b, model-c")
    assert result == ("model-a", "model-b", "model-c")


def test_fallback_models_filters_blanks():
    """_to_fallback_models strips whitespace and filters empty items."""
    result = _to_fallback_models(["model-a", "  ", "", "model-b"])
    assert result == ("model-a", "model-b")


def test_fallback_models_non_list_non_string():
    """_to_fallback_models returns empty tuple for unsupported types."""
    assert _to_fallback_models(42) == ()
    assert _to_fallback_models(None) == ()


# ---------------------------------------------------------------------------
# _to_string_tuple
# ---------------------------------------------------------------------------


def test_string_tuple_from_list():
    """_to_string_tuple normalizes a list into a tuple of strings."""
    result = _to_string_tuple(["nebius", "together"])
    assert result == ("nebius", "together")


def test_string_tuple_from_csv():
    """_to_string_tuple parses comma-separated string."""
    result = _to_string_tuple("nebius, together")
    assert result == ("nebius", "together")


def test_string_tuple_filters_blanks():
    """_to_string_tuple strips empty items."""
    result = _to_string_tuple(["nebius", "", "  "])
    assert result == ("nebius",)


def test_string_tuple_unsupported_type():
    """_to_string_tuple returns empty tuple for non-list/string."""
    assert _to_string_tuple(123) == ()


# ---------------------------------------------------------------------------
# _parse_string_table
# ---------------------------------------------------------------------------


def test_parse_string_table_simple():
    """_parse_string_table handles name = 'path' entries."""
    raw = {"claude": "~/.claude/projects", "codex": "~/.codex/sessions"}
    result = _parse_string_table(raw)
    assert result == {"claude": "~/.claude/projects", "codex": "~/.codex/sessions"}


def test_parse_string_table_dict_entries():
    """_parse_string_table handles name = {path = '...'} entries."""
    raw = {"claude": {"path": "/home/user/.claude"}}
    result = _parse_string_table(raw)
    assert result == {"claude": "/home/user/.claude"}


def test_parse_string_table_skips_empty():
    """_parse_string_table skips entries with empty/None values."""
    raw = {"good": "/path", "bad": "", "none": None}
    result = _parse_string_table(raw)
    assert result == {"good": "/path"}


# ---------------------------------------------------------------------------
# _toml_value
# ---------------------------------------------------------------------------


def test_toml_value_bool():
    """_toml_value serializes booleans to TOML true/false."""
    assert _toml_value(True) == "true"
    assert _toml_value(False) == "false"


def test_toml_value_int():
    """_toml_value serializes integers as plain numbers."""
    assert _toml_value(42) == "42"


def test_toml_value_float():
    """_toml_value serializes floats as plain numbers."""
    assert _toml_value(3.14) == "3.14"


def test_toml_value_string():
    """_toml_value serializes strings with double quotes."""
    assert _toml_value("hello") == '"hello"'


def test_toml_value_string_escapes():
    """_toml_value escapes backslashes and quotes in strings."""
    assert _toml_value('say "hi"') == '"say \\"hi\\""'


def test_toml_value_list():
    """_toml_value serializes lists with brackets."""
    assert _toml_value(["a", "b"]) == '["a", "b"]'


def test_toml_value_tuple():
    """_toml_value serializes tuples like lists."""
    assert _toml_value(("x", "y")) == '["x", "y"]'


# ---------------------------------------------------------------------------
# _toml_write_dict
# ---------------------------------------------------------------------------


def test_toml_write_dict_flat():
    """_toml_write_dict writes scalar key=value lines."""
    lines: list[str] = []
    _toml_write_dict(lines, {"key": "val", "num": 42}, prefix="section")
    text = "".join(lines)
    assert 'key = "val"' in text
    assert "num = 42" in text


def test_toml_write_dict_nested():
    """_toml_write_dict creates [section.subsection] headers for nested dicts."""
    lines: list[str] = []
    _toml_write_dict(lines, {"sub": {"key": "val"}}, prefix="parent")
    text = "".join(lines)
    assert "[parent.sub]" in text
    assert 'key = "val"' in text


# ---------------------------------------------------------------------------
# save_config_patch
# ---------------------------------------------------------------------------


def test_save_config_patch_roundtrip(tmp_path, monkeypatch):
    """save_config_patch writes TOML and reload reads it back."""
    config_path = tmp_path / "config.toml"
    monkeypatch.setattr("lerim.config.settings.USER_CONFIG_PATH", config_path)
    cfg = save_config_patch({"server": {"port": 9999}})
    assert cfg.server_port == 9999


def test_save_config_patch_deep_merges(tmp_path, monkeypatch):
    """save_config_patch deep-merges with existing config."""
    config_path = tmp_path / "config.toml"
    config_path.write_text('[server]\nhost = "0.0.0.0"\n', encoding="utf-8")
    monkeypatch.setattr("lerim.config.settings.USER_CONFIG_PATH", config_path)
    cfg = save_config_patch({"server": {"port": 9999}})
    assert cfg.server_host == "0.0.0.0"
    assert cfg.server_port == 9999


# ---------------------------------------------------------------------------
# Layer precedence
# ---------------------------------------------------------------------------


def test_layer_precedence_explicit_overrides(tmp_path, monkeypatch):
    """LERIM_CONFIG env var layer overrides all other layers."""
    explicit = tmp_path / "explicit.toml"
    explicit.write_text('[server]\nport = 1234\n', encoding="utf-8")
    monkeypatch.setenv("LERIM_CONFIG", str(explicit))
    cfg = reload_config()
    assert cfg.server_port == 1234


def test_deep_merge_adds_new_keys():
    """_deep_merge adds keys from override that don't exist in base."""
    base = {"a": 1}
    override = {"b": 2}
    result = _deep_merge(base, override)
    assert result == {"a": 1, "b": 2}


def test_deep_merge_replaces_non_dict_with_dict():
    """_deep_merge replaces scalar with dict when override has dict."""
    base = {"a": 1}
    override = {"a": {"nested": True}}
    result = _deep_merge(base, override)
    assert result == {"a": {"nested": True}}


# ---------------------------------------------------------------------------
# Port clamping
# ---------------------------------------------------------------------------


def test_port_over_65535_resets(tmp_path, monkeypatch):
    """Port > 65535 resets to default 8765."""
    explicit = tmp_path / "bad_port.toml"
    explicit.write_text('[server]\nport = 99999\n', encoding="utf-8")
    monkeypatch.setenv("LERIM_CONFIG", str(explicit))
    cfg = reload_config()
    assert cfg.server_port == 8765


# ---------------------------------------------------------------------------
# Role builder edge cases
# ---------------------------------------------------------------------------


def test_llm_role_explicit_overrides():
    """_build_llm_role uses explicit values over defaults."""
    role = _build_llm_role(
        {"provider": "anthropic", "model": "claude-3", "timeout_seconds": 600},
        default_provider="openrouter",
        default_model="default-model",
    )
    assert role.provider == "anthropic"
    assert role.model == "claude-3"
    assert role.timeout_seconds == 600


def test_llm_role_timeout_minimum():
    """_build_llm_role enforces minimum timeout of 30s."""
    role = _build_llm_role(
        {"timeout_seconds": 5},
        default_provider="openrouter",
        default_model="m",
    )
    assert role.timeout_seconds == 30


def test_dspy_role_sub_defaults_to_main():
    """_build_dspy_role sub_provider/sub_model default to main provider/model."""
    role = _build_dspy_role(
        {"provider": "ollama", "model": "qwen3:8b"},
        default_provider="openrouter",
        default_model="default",
    )
    assert role.sub_provider == "ollama"
    assert role.sub_model == "qwen3:8b"


def test_dspy_role_explicit_sub():
    """_build_dspy_role uses explicit sub_provider/sub_model when set."""
    role = _build_dspy_role(
        {"provider": "ollama", "model": "qwen3:8b",
         "sub_provider": "openrouter", "sub_model": "cheap-model"},
        default_provider="openrouter",
        default_model="default",
    )
    assert role.sub_provider == "openrouter"
    assert role.sub_model == "cheap-model"


def test_dspy_role_max_llm_calls_minimum():
    """_build_dspy_role enforces minimum max_llm_calls of 1."""
    role = _build_dspy_role(
        {"max_llm_calls": -5},
        default_provider="ollama",
        default_model="m",
    )
    assert role.max_llm_calls == 1
