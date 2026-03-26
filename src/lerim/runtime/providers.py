"""Provider builders for DSPy pipelines and shared provider utilities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import dspy

from lerim.config.settings import Config, DSPyRoleConfig, LLMRoleConfig, get_config

RoleName = Literal["lead"]
DSPyRoleName = Literal["extract", "summarize"]


@dataclass(frozen=True)
class FallbackSpec:
	"""Parsed fallback descriptor used for model-chain construction."""

	provider: str
	model: str


def _role_config(config: Config, role: RoleName) -> LLMRoleConfig:
	"""Return orchestration role config from runtime config."""
	return config.lead_role


def _dspy_role_config(config: Config, role: DSPyRoleName) -> DSPyRoleConfig:
	"""Return DSPy role config from runtime config."""
	return config.extract_role if role == "extract" else config.summarize_role


def _default_api_base(provider: str, config: Config | None = None) -> str:
	"""Return provider default API base from config's [providers] section."""
	if config is None:
		config = get_config()
	return config.provider_api_bases.get(provider, "")


def _api_key_for_provider(config: Config, provider: str) -> str | None:
	"""Resolve API key for a provider from environment-backed config."""
	if provider == "zai":
		return config.zai_api_key
	if provider == "openrouter":
		return config.openrouter_api_key
	if provider == "openai":
		return config.openai_api_key
	if provider == "anthropic":
		return config.anthropic_api_key
	if provider == "minimax":
		return config.minimax_api_key
	if provider == "opencode_go":
		return config.opencode_api_key
	return None


def parse_fallback_spec(
	raw: str, *, default_provider: str = "openrouter"
) -> FallbackSpec:
	"""Parse fallback descriptor in the format ``provider:model`` or ``model``."""
	text = str(raw).strip()
	if not text:
		raise RuntimeError("fallback_model_empty")
	if ":" not in text:
		return FallbackSpec(provider=default_provider, model=text)
	provider, model = text.split(":", 1)
	provider = provider.strip().lower()
	model = model.strip()
	if not provider or not model:
		raise RuntimeError(f"fallback_model_invalid:{raw}")
	return FallbackSpec(provider=provider, model=model)


def _build_dspy_lm_for_provider(
	*,
	provider: str,
	model: str,
	api_base: str,
	cfg: Config,
	role_label: str,
	openrouter_provider_order: tuple[str, ...] = (),
	thinking: bool = True,
	max_tokens: int = 32000,
) -> dspy.LM:
	"""Build a single DSPy LM object from provider/model/api_base."""
	if provider == "ollama":
		kwargs: dict = dict(
			api_key="ollama",
			api_base=api_base or _default_api_base("ollama"),
			cache=False,
			max_tokens=max_tokens,
		)
		if not thinking:
			kwargs["reasoning_effort"] = "none"
		return dspy.LM(f"ollama_chat/{model}", **kwargs)
	if provider == "mlx":
		return dspy.LM(
			f"openai/{model}",
			api_key="mlx",
			api_base=api_base or _default_api_base("mlx"),
			cache=False,
			max_tokens=max_tokens,
		)
	if provider == "openrouter":
		api_key = _api_key_for_provider(cfg, "openrouter")
		if not api_key:
			raise RuntimeError(
				f"missing_api_key:OPENROUTER_API_KEY required for {role_label}"
			)
		extra_body: dict | None = None
		if openrouter_provider_order:
			extra_body = {"provider": {"order": list(openrouter_provider_order)}}
		return dspy.LM(
			f"openrouter/{model}",
			api_key=api_key,
			api_base=api_base or _default_api_base("openrouter"),
			cache=False,
			max_tokens=max_tokens,
			extra_body=extra_body,
		)
	if provider == "opencode_go":
		api_key = _api_key_for_provider(cfg, "opencode_go")
		if not api_key:
			raise RuntimeError(f"missing_api_key:OPENCODE_API_KEY required for {role_label}")
		base = api_base or _default_api_base("opencode_go")
		# kimi/glm models use chat completions, minimax models use anthropic messages
		if any(model.startswith(p) for p in ("minimax",)):
			return dspy.LM(f"anthropic/{model}", api_key=api_key, api_base=base, cache=False, max_tokens=max_tokens)
		return dspy.LM(f"openai/{model}", api_key=api_key, api_base=base, cache=False, max_tokens=max_tokens)
	if provider in {"zai", "openai", "minimax"}:
		api_key = _api_key_for_provider(cfg, provider)
		env_name = {
			"zai": "ZAI_API_KEY",
			"openai": "OPENAI_API_KEY",
			"minimax": "MINIMAX_API_KEY",
		}[provider]
		if not api_key:
			raise RuntimeError(f"missing_api_key:{env_name} required for {role_label}")
		return dspy.LM(
			f"openai/{model}",
			api_key=api_key,
			api_base=api_base or _default_api_base(provider),
			cache=False,
			max_tokens=max_tokens,
		)
	raise RuntimeError(f"unsupported_dspy_provider:{provider}")


def build_dspy_lm(
	role: DSPyRoleName,
	*,
	config: Config | None = None,
) -> dspy.LM:
	"""Build a DSPy LM object for extract/summarize roles.

	Returns the LM without calling dspy.configure() globally.
	Callers should use dspy.context(lm=lm) for thread-safe execution.
	"""
	cfg = config or get_config()
	role_cfg = _dspy_role_config(cfg, role)
	return _build_dspy_lm_for_provider(
		provider=role_cfg.provider.strip().lower(),
		model=role_cfg.model,
		api_base=role_cfg.api_base,
		cfg=cfg,
		role_label=f"roles.{role}.provider={role_cfg.provider}",
		openrouter_provider_order=role_cfg.openrouter_provider_order,
		thinking=role_cfg.thinking,
		max_tokens=role_cfg.max_tokens,
	)


def build_dspy_fallback_lms(
	role: DSPyRoleName,
	*,
	config: Config | None = None,
) -> list[dspy.LM]:
	"""Build fallback DSPy LMs from role config's fallback_models."""
	cfg = config or get_config()
	role_cfg = _dspy_role_config(cfg, role)
	specs = [parse_fallback_spec(item) for item in role_cfg.fallback_models]
	return [
		_build_dspy_lm_for_provider(
			provider=spec.provider.strip().lower(),
			model=spec.model,
			api_base="",
			cfg=cfg,
			role_label=f"roles.{role}.fallback={spec.provider}:{spec.model}",
			thinking=role_cfg.thinking,
			max_tokens=role_cfg.max_tokens,
		)
		for spec in specs
	]


def list_provider_models(provider: str) -> list[str]:
	"""Return static provider model suggestions for dashboard UI selections."""
	normalized = str(provider).strip().lower()
	options = {
		"zai": ["glm-4.7", "glm-4.5-air", "glm-4.5"],
		"minimax": ["MiniMax-M2.5", "MiniMax-M2.1", "MiniMax-M2"],
		"openrouter": [
			"qwen/qwen3-coder-30b-a3b-instruct",
			"anthropic/claude-sonnet-4-5-20250929",
			"anthropic/claude-haiku-4-5-20251001",
		],
		"openai": ["gpt-5-mini", "gpt-5"],
		"ollama": ["qwen3:8b", "qwen3:4b", "qwen3:14b"],
		"mlx": [
			"mlx-community/Qwen3.5-9B-4bit",
			"mlx-community/Qwen3.5-27B-4bit",
			"mlx-community/Qwen3.5-35B-A3B-4bit",
		],
		"opencode_go": ["kimi-k2.5", "glm-5", "minimax-m2.7", "minimax-m2.5"],
	}
	return list(options.get(normalized, []))


if __name__ == "__main__":
	"""Run provider-layer self-test for shared utilities and DSPy builders."""
	cfg = get_config()

	# -- shared utility tests --
	spec = parse_fallback_spec("openrouter:anthropic/claude-sonnet-4-5-20250929")
	assert spec.provider == "openrouter"
	assert spec.model == "anthropic/claude-sonnet-4-5-20250929"

	spec_default = parse_fallback_spec("some-model")
	assert spec_default.provider == "openrouter"
	assert spec_default.model == "some-model"

	assert isinstance(list_provider_models("ollama"), list)

	# -- DSPy builder test --
	dspy_model = build_dspy_lm("extract", config=cfg)
	assert isinstance(dspy_model, dspy.LM)

	print(
		f"""\
providers: \
extract={cfg.extract_role.provider}/{cfg.extract_role.model} \
summarize={cfg.summarize_role.provider}/{cfg.summarize_role.model}"""
	)
