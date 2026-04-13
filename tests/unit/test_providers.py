"""Unit tests for provider builders (PydanticAI-only)."""

from __future__ import annotations

from dataclasses import replace

import pytest
from pydantic_ai.models.fallback import FallbackModel

from lerim.config.providers import (
	_api_key_for_provider,
	build_pydantic_model,
	build_pydantic_model_from_provider,
	list_provider_models,
	normalize_model_name,
	parse_fallback_spec,
)
from lerim.config.settings import RoleConfig
from tests.helpers import make_config


def test_parse_fallback_spec_with_provider() -> None:
	spec = parse_fallback_spec("zai:glm-4.7")
	assert spec.provider == "zai"
	assert spec.model == "glm-4.7"


def test_parse_fallback_spec_without_provider_uses_default_openrouter() -> None:
	spec = parse_fallback_spec("x-ai/grok-4.1-fast")
	assert spec.provider == "openrouter"
	assert spec.model == "x-ai/grok-4.1-fast"


def test_parse_fallback_spec_normalizes_known_model_casing() -> None:
	spec = parse_fallback_spec("minimax:minimax-m2.5")
	assert spec.provider == "minimax"
	assert spec.model == "MiniMax-M2.5"


def test_normalize_model_name_known_and_unknown() -> None:
	assert normalize_model_name("minimax", "minimax-m2.7") == "MiniMax-M2.7"
	assert normalize_model_name("openrouter", "any/model") == "any/model"


def test_api_key_resolution(tmp_path) -> None:
	cfg = make_config(tmp_path)
	cfg = replace(
		cfg,
		zai_api_key="z-key",
		openrouter_api_key="or-key",
		openai_api_key="oa-key",
		minimax_api_key="mm-key",
		opencode_api_key="oc-key",
	)
	assert _api_key_for_provider(cfg, "zai") == "z-key"
	assert _api_key_for_provider(cfg, "openrouter") == "or-key"
	assert _api_key_for_provider(cfg, "openai") == "oa-key"
	assert _api_key_for_provider(cfg, "minimax") == "mm-key"
	assert _api_key_for_provider(cfg, "opencode_go") == "oc-key"
	assert _api_key_for_provider(cfg, "ollama") is None


def test_build_pydantic_model_missing_api_key_raises(tmp_path) -> None:
	cfg = make_config(tmp_path)
	cfg = replace(
		cfg,
		agent_role=RoleConfig(provider="openrouter", model="x-ai/grok-4.1-fast"),
		openrouter_api_key=None,
	)
	with pytest.raises(RuntimeError, match="missing_api_key"):
		build_pydantic_model("agent", config=cfg)


def test_build_pydantic_model_ollama_no_key(tmp_path) -> None:
	cfg = make_config(tmp_path)
	cfg = replace(
		cfg,
		agent_role=RoleConfig(provider="ollama", model="qwen3:8b"),
	)
	model = build_pydantic_model("agent", config=cfg)
	assert model is not None


def test_build_pydantic_model_skips_unavailable_fallback_keys(tmp_path) -> None:
	"""Fallbacks with missing keys should be skipped, not fail the build."""
	cfg = make_config(tmp_path)
	cfg = replace(
		cfg,
		agent_role=RoleConfig(
			provider="ollama",
			model="qwen3:8b",
			fallback_models=("openrouter:x-ai/grok-4.1-fast",),
		),
		openrouter_api_key=None,
	)
	model = build_pydantic_model("agent", config=cfg)
	assert model is not None
	assert not isinstance(model, FallbackModel)


def test_build_pydantic_model_with_fallback_chain(tmp_path) -> None:
	cfg = make_config(tmp_path)
	cfg = replace(
		cfg,
		agent_role=RoleConfig(
			provider="openrouter",
			model="x-ai/grok-4.1-fast",
			fallback_models=("zai:glm-4.7",),
		),
		openrouter_api_key="or-key",
		zai_api_key="z-key",
	)
	model = build_pydantic_model("agent", config=cfg)
	assert isinstance(model, FallbackModel)
	assert len(model.models) == 2


def test_build_pydantic_model_from_provider(tmp_path) -> None:
	cfg = make_config(tmp_path)
	cfg = replace(cfg, openrouter_api_key="or-key")
	model = build_pydantic_model_from_provider(
		"openrouter",
		"x-ai/grok-4.1-fast",
		config=cfg,
	)
	assert model is not None


def test_build_pydantic_model_from_provider_with_fallbacks(tmp_path) -> None:
	cfg = make_config(tmp_path)
	cfg = replace(cfg, openrouter_api_key="or-key", zai_api_key="z-key")
	model = build_pydantic_model_from_provider(
		"openrouter",
		"x-ai/grok-4.1-fast",
		fallback_models=["zai:glm-4.7"],
		config=cfg,
	)
	assert isinstance(model, FallbackModel)
	assert len(model.models) == 2


def test_list_provider_models_known_and_unknown() -> None:
	for provider in ("zai", "openrouter", "openai", "ollama", "mlx", "minimax"):
		assert list_provider_models(provider)
	assert list_provider_models("unknown") == []
