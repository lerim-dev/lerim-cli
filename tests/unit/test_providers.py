"""Unit tests for provider builders (DSPy pipelines and lead agent LM)."""

from __future__ import annotations

import pytest

from lerim.config.settings import RoleConfig
from lerim.config.providers import (
    build_dspy_lm,
    list_provider_models,
    parse_fallback_spec,
)
from tests.helpers import make_config


def test_parse_fallback_spec_with_provider():
    """'zai:glm-4.7-flash' -> FallbackSpec(provider='zai', model='glm-4.7-flash')."""
    spec = parse_fallback_spec("zai:glm-4.7-flash")
    assert spec.provider == "zai"
    assert spec.model == "glm-4.7-flash"


def test_parse_fallback_spec_without_provider():
    """'glm-4.7-flash' -> FallbackSpec(provider=default, model='glm-4.7-flash')."""
    spec = parse_fallback_spec("glm-4.7-flash")
    assert spec.provider == "openrouter"
    assert spec.model == "glm-4.7-flash"


def test_parse_fallback_spec_normalizes_minimax_casing():
    """'minimax:minimax-m2.5' (lowercase) -> FallbackSpec with PascalCase model."""
    spec = parse_fallback_spec("minimax:minimax-m2.5")
    assert spec.provider == "minimax"
    assert spec.model == "MiniMax-M2.5"


def test_build_dspy_lm_ollama(tmp_path):
    """build_dspy_lm with ollama provider constructs 'ollama_chat/model' LM."""
    import dspy

    cfg = make_config(tmp_path)
    from dataclasses import replace
    from lerim.config.settings import RoleConfig as _RC

    ollama_role = _RC(
        provider="ollama",
        model="qwen3:4b",
        timeout_seconds=120,
        max_window_tokens=300000,
        window_overlap_tokens=5000,
    )
    cfg = replace(cfg, lead_role=ollama_role)
    lm = build_dspy_lm("lead", config=cfg)
    assert isinstance(lm, dspy.LM)


def test_build_dspy_lm_openrouter(tmp_path):
    """build_dspy_lm with openrouter provider constructs correct LM."""
    import dspy
    from dataclasses import replace
    from lerim.config.settings import RoleConfig

    cfg = make_config(tmp_path)
    cfg = replace(cfg, openrouter_api_key="test-key")
    or_role = RoleConfig(
        provider="openrouter",
        model="test/model",
        timeout_seconds=120,
        max_window_tokens=300000,
        window_overlap_tokens=5000,
        openrouter_provider_order=("nebius",),
    )
    cfg = replace(cfg, lead_role=or_role)
    lm = build_dspy_lm("lead", config=cfg)
    assert isinstance(lm, dspy.LM)


def test_build_dspy_lm_zai(tmp_path):
    """build_dspy_lm with zai provider constructs correct LM."""
    import dspy
    from dataclasses import replace
    from lerim.config.settings import RoleConfig

    cfg = make_config(tmp_path)
    zai_role = RoleConfig(
        provider="zai",
        model="glm-4.5-air",
        timeout_seconds=120,
        max_window_tokens=300000,
        window_overlap_tokens=5000,
    )
    cfg = replace(cfg, lead_role=zai_role, zai_api_key="test-key")
    lm = build_dspy_lm("lead", config=cfg)
    assert isinstance(lm, dspy.LM)


def test_build_dspy_lm_mlx(tmp_path):
    """build_dspy_lm with mlx provider constructs 'openai/model' LM."""
    import dspy
    from dataclasses import replace
    from lerim.config.settings import RoleConfig

    cfg = make_config(tmp_path)
    mlx_role = RoleConfig(
        provider="mlx",
        model="mlx-community/Qwen3.5-9B-4bit",
        timeout_seconds=120,
        max_window_tokens=300000,
        window_overlap_tokens=5000,
    )
    cfg = replace(cfg, lead_role=mlx_role)
    lm = build_dspy_lm("lead", config=cfg)
    assert isinstance(lm, dspy.LM)


def test_api_key_mlx_returns_none(tmp_path):
    """MLX provider does not require an API key."""
    from lerim.config.providers import _api_key_for_provider

    cfg = make_config(tmp_path)
    assert _api_key_for_provider(cfg, "mlx") is None


def test_api_key_resolution(tmp_path):
    """_api_key_for_provider resolves from config fields."""
    from dataclasses import replace
    from lerim.config.providers import _api_key_for_provider

    cfg = make_config(tmp_path)
    cfg = replace(cfg, zai_api_key="zai-key-123")
    assert _api_key_for_provider(cfg, "zai") == "zai-key-123"
    assert _api_key_for_provider(cfg, "ollama") is None


def test_missing_api_key_raises(tmp_path):
    """Missing API key for non-ollama provider raises error."""
    from dataclasses import replace
    from lerim.config.settings import RoleConfig

    cfg = make_config(tmp_path)
    cfg = replace(cfg, openrouter_api_key=None)
    or_role = RoleConfig(
        provider="openrouter",
        model="test/model",
        timeout_seconds=120,
        max_window_tokens=300000,
        window_overlap_tokens=5000,
    )
    cfg = replace(cfg, lead_role=or_role)
    with pytest.raises(RuntimeError, match="missing_api_key"):
        build_dspy_lm("lead", config=cfg)


def test_build_dspy_lm_lead(tmp_path):
    """build_dspy_lm with 'lead' role should construct a DSPy LM."""
    import dspy
    from dataclasses import replace
    from lerim.config.providers import build_dspy_lm

    cfg = make_config(tmp_path)
    cfg = replace(cfg, openrouter_api_key="test-key")
    lm = build_dspy_lm("lead", config=cfg)
    assert isinstance(lm, dspy.LM)


def test_build_dspy_fallback_lms_lead_empty(tmp_path):
    """build_dspy_fallback_lms with 'lead' role and no fallbacks returns empty list."""
    from lerim.config.providers import build_dspy_fallback_lms

    cfg = make_config(tmp_path)
    lms = build_dspy_fallback_lms("lead", config=cfg)
    assert isinstance(lms, list)
    assert lms == []


def test_build_dspy_fallback_lms_lead_with_fallbacks(tmp_path):
    """build_dspy_fallback_lms with 'lead' role and fallbacks returns LM list."""
    import dspy
    from dataclasses import replace
    from lerim.config.providers import build_dspy_fallback_lms

    cfg = make_config(tmp_path)
    role = RoleConfig(
        provider="openrouter",
        model="x-ai/grok-4.1-fast",
        fallback_models=("openrouter:qwen/qwen3-coder",),
        timeout_seconds=120,
    )
    cfg = replace(cfg, lead_role=role, openrouter_api_key="test-key")
    lms = build_dspy_fallback_lms("lead", config=cfg)
    assert len(lms) == 1
    assert isinstance(lms[0], dspy.LM)


def test_list_provider_models():
    """list_provider_models returns non-empty list for known providers."""
    for provider in ("zai", "openrouter", "openai", "ollama", "mlx", "minimax"):
        models = list_provider_models(provider)
        assert len(models) > 0, f"No models for {provider}"
    # Unknown provider returns empty
    assert list_provider_models("unknown") == []
