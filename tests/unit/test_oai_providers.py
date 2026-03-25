"""Unit tests for OpenAI Agents SDK provider builders."""

from __future__ import annotations

from dataclasses import replace

import pytest
from agents.extensions.models.litellm_model import LitellmModel

from lerim.config.settings import LLMRoleConfig
from lerim.runtime.oai_providers import (
	build_codex_options,
	build_oai_fallback_models,
	build_oai_model,
	build_oai_model_from_role,
)
from tests.helpers import make_config


def _minimax_role(**overrides) -> LLMRoleConfig:
	"""Build a MiniMax role config for testing."""
	defaults = dict(
		provider="minimax",
		model="MiniMax-M2.5",
		api_base="",
		fallback_models=(),
		timeout_seconds=120,
		max_iterations=50,
		openrouter_provider_order=(),
		thinking=True,
		max_tokens=32000,
		max_explorers=4,
	)
	defaults.update(overrides)
	return LLMRoleConfig(**defaults)


def test_build_oai_model_returns_litellm_model(tmp_path):
	"""build_oai_model should return a LitellmModel instance."""
	cfg = make_config(tmp_path)
	model = build_oai_model("lead", config=cfg)
	assert isinstance(model, LitellmModel)


def test_build_oai_model_minimax(tmp_path):
	"""MiniMax provider should produce 'minimax/MODEL' format."""
	cfg = make_config(tmp_path)
	role = _minimax_role()
	cfg = replace(cfg, lead_role=role, minimax_api_key="test-key")
	model = build_oai_model_from_role(role, config=cfg)
	assert isinstance(model, LitellmModel)


def test_build_oai_model_openrouter(tmp_path):
	"""OpenRouter provider should produce 'openrouter/MODEL' format."""
	cfg = make_config(tmp_path)
	role = _minimax_role(provider="openrouter", model="qwen/qwen3-coder")
	cfg = replace(cfg, lead_role=role, openrouter_api_key="test-key")
	model = build_oai_model_from_role(role, config=cfg)
	assert isinstance(model, LitellmModel)


def test_build_oai_model_zai(tmp_path):
	"""ZAI provider should produce 'openai/MODEL' format with custom base_url."""
	cfg = make_config(tmp_path)
	role = _minimax_role(provider="zai", model="glm-4.7")
	cfg = replace(cfg, lead_role=role, zai_api_key="test-key")
	model = build_oai_model_from_role(role, config=cfg)
	assert isinstance(model, LitellmModel)


def test_build_oai_model_openai(tmp_path):
	"""OpenAI provider should produce 'openai/MODEL' format."""
	cfg = make_config(tmp_path)
	role = _minimax_role(provider="openai", model="gpt-5-mini")
	cfg = replace(cfg, lead_role=role, openai_api_key="test-key")
	model = build_oai_model_from_role(role, config=cfg)
	assert isinstance(model, LitellmModel)


def test_build_oai_model_ollama(tmp_path):
	"""Ollama provider should produce 'ollama_chat/MODEL' format."""
	cfg = make_config(tmp_path)
	role = _minimax_role(provider="ollama", model="qwen3:8b")
	cfg = replace(cfg, lead_role=role)
	model = build_oai_model_from_role(role, config=cfg)
	assert isinstance(model, LitellmModel)


def test_build_oai_model_unsupported_raises(tmp_path):
	"""Unsupported provider should raise RuntimeError."""
	cfg = make_config(tmp_path)
	role = _minimax_role(provider="unknown_provider", model="x")
	with pytest.raises(RuntimeError, match="unsupported_oai_provider"):
		build_oai_model_from_role(role, config=cfg)


def test_build_oai_fallback_models_empty(tmp_path):
	"""No fallbacks configured should return empty list."""
	cfg = make_config(tmp_path)
	role = _minimax_role(fallback_models=())
	result = build_oai_fallback_models(role, config=cfg)
	assert result == []


def test_build_oai_fallback_models_multiple(tmp_path):
	"""Multiple fallbacks should return list of LitellmModel."""
	cfg = make_config(tmp_path)
	cfg = replace(cfg, openrouter_api_key="test-key", zai_api_key="test-key")
	role = _minimax_role(fallback_models=("openrouter:qwen/qwen3-coder", "zai:glm-4.7"))
	result = build_oai_fallback_models(role, config=cfg)
	assert len(result) == 2
	assert all(isinstance(m, LitellmModel) for m in result)


# -- Codex options tests --


def test_build_codex_options_openrouter(tmp_path):
	"""OpenRouter provider should use OpenRouter directly — no proxy needed."""
	cfg = make_config(tmp_path)
	cfg = replace(cfg, openrouter_api_key="test-or-key", lead_role=_minimax_role(provider="openrouter", model="qwen/qwen3-coder"))
	codex_opts, thread_opts, needs_proxy = build_codex_options(config=cfg)
	assert needs_proxy is False
	assert codex_opts["api_key"] == "test-or-key"
	assert "openrouter" in codex_opts["base_url"]
	assert thread_opts["model"] == "qwen/qwen3-coder"


def test_build_codex_options_minimax_needs_proxy(tmp_path):
	"""MiniMax provider should need a proxy (Responses API not supported)."""
	cfg = make_config(tmp_path)
	cfg = replace(cfg, minimax_api_key="test-mm-key", lead_role=_minimax_role())
	codex_opts, thread_opts, needs_proxy = build_codex_options(config=cfg)
	assert needs_proxy is True
	assert codex_opts["backend_api_key"] == "test-mm-key"
	assert "minimax" in codex_opts["backend_url"]
	assert thread_opts["model"] == "MiniMax-M2.5"


def test_build_codex_options_openai_direct(tmp_path):
	"""OpenAI provider should use OpenAI directly — no proxy needed."""
	cfg = make_config(tmp_path)
	cfg = replace(cfg, openai_api_key="test-oai-key", lead_role=_minimax_role(provider="openai", model="gpt-5-mini"))
	codex_opts, thread_opts, needs_proxy = build_codex_options(config=cfg)
	assert needs_proxy is False
	assert codex_opts["api_key"] == "test-oai-key"
	assert thread_opts["model"] == "gpt-5-mini"


def test_build_codex_options_zai_needs_proxy(tmp_path):
	"""ZAI provider should need a proxy."""
	cfg = make_config(tmp_path)
	cfg = replace(cfg, zai_api_key="test-zai-key", lead_role=_minimax_role(provider="zai", model="glm-4.7"))
	codex_opts, thread_opts, needs_proxy = build_codex_options(config=cfg)
	assert needs_proxy is True
	assert codex_opts["backend_api_key"] == "test-zai-key"
	assert thread_opts["model"] == "glm-4.7"
