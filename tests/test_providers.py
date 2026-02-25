"""Unit tests for provider builders (PydanticAI orchestration and DSPy pipelines)."""

from __future__ import annotations

import pytest

from lerim.runtime.providers import (
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


def test_build_dspy_lm_ollama(tmp_path):
    """build_dspy_lm with ollama provider constructs 'ollama_chat/model' LM."""
    import dspy

    cfg = make_config(tmp_path)
    # Override extract role to use ollama
    from dataclasses import replace
    from lerim.config.settings import DSPyRoleConfig

    ollama_role = DSPyRoleConfig(
        provider="ollama",
        model="qwen3:4b",
        api_base="",
        timeout_seconds=120,
        max_iterations=24,
        max_llm_calls=24,
        sub_provider="ollama",
        sub_model="qwen3:4b",
        openrouter_provider_order=(),
    )
    cfg = replace(cfg, extract_role=ollama_role)
    lm = build_dspy_lm("extract", config=cfg)
    assert isinstance(lm, dspy.LM)


def test_build_dspy_lm_openrouter(tmp_path):
    """build_dspy_lm with openrouter provider constructs correct LM."""
    import dspy
    from dataclasses import replace
    from lerim.config.settings import DSPyRoleConfig

    cfg = make_config(tmp_path)
    cfg = replace(cfg, openrouter_api_key="test-key")
    or_role = DSPyRoleConfig(
        provider="openrouter",
        model="test/model",
        api_base="",
        timeout_seconds=120,
        max_iterations=24,
        max_llm_calls=24,
        sub_provider="openrouter",
        sub_model="test/model",
        openrouter_provider_order=("nebius",),
    )
    cfg = replace(cfg, extract_role=or_role)
    lm = build_dspy_lm("extract", config=cfg)
    assert isinstance(lm, dspy.LM)


def test_build_dspy_lm_zai(tmp_path):
    """build_dspy_lm with zai provider constructs correct LM."""
    import dspy
    from dataclasses import replace
    from lerim.config.settings import DSPyRoleConfig

    cfg = make_config(tmp_path)
    zai_role = DSPyRoleConfig(
        provider="zai",
        model="glm-4.5-air",
        api_base="",
        timeout_seconds=120,
        max_iterations=24,
        max_llm_calls=24,
        sub_provider="zai",
        sub_model="glm-4.5-air",
        openrouter_provider_order=(),
    )
    cfg = replace(cfg, extract_role=zai_role, zai_api_key="test-key")
    lm = build_dspy_lm("extract", config=cfg)
    assert isinstance(lm, dspy.LM)


def test_build_orchestration_model_with_fallback(tmp_path):
    """build_orchestration_model with fallback_models returns FallbackModel."""
    from dataclasses import replace
    from pydantic_ai.models.fallback import FallbackModel
    from lerim.config.settings import LLMRoleConfig
    from lerim.runtime.providers import build_orchestration_model_from_role

    cfg = make_config(tmp_path)
    cfg = replace(cfg, zai_api_key="test-key", openrouter_api_key="test-key")
    role = LLMRoleConfig(
        provider="openrouter",
        model="qwen/qwen3-coder-30b-a3b-instruct",
        api_base="",
        fallback_models=("openrouter:anthropic/claude-haiku-4-5-20251001",),
        timeout_seconds=300,
        max_iterations=24,
        openrouter_provider_order=("nebius",),
    )
    model = build_orchestration_model_from_role(role, config=cfg)
    assert isinstance(model, FallbackModel)


def test_api_key_resolution(tmp_path):
    """_api_key_for_provider resolves from config fields."""
    from dataclasses import replace
    from lerim.runtime.providers import _api_key_for_provider

    cfg = make_config(tmp_path)
    cfg = replace(cfg, zai_api_key="zai-key-123")
    assert _api_key_for_provider(cfg, "zai") == "zai-key-123"
    assert _api_key_for_provider(cfg, "ollama") is None


def test_missing_api_key_raises(tmp_path):
    """Missing API key for non-ollama provider raises error."""
    from dataclasses import replace
    from lerim.config.settings import DSPyRoleConfig

    cfg = make_config(tmp_path)
    cfg = replace(cfg, openrouter_api_key=None)
    or_role = DSPyRoleConfig(
        provider="openrouter",
        model="test/model",
        api_base="",
        timeout_seconds=120,
        max_iterations=24,
        max_llm_calls=24,
        sub_provider="openrouter",
        sub_model="test/model",
        openrouter_provider_order=(),
    )
    cfg = replace(cfg, extract_role=or_role)
    with pytest.raises(RuntimeError, match="missing_api_key"):
        build_dspy_lm("extract", config=cfg)


def test_list_provider_models():
    """list_provider_models returns non-empty list for known providers."""
    for provider in ("zai", "openrouter", "openai", "ollama"):
        models = list_provider_models(provider)
        assert len(models) > 0, f"No models for {provider}"
    # Unknown provider returns empty
    assert list_provider_models("unknown") == []
