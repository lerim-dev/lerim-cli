"""Provider capability registry and validation for lerim roles.

Each provider declares which roles it supports and any model-level restrictions.
"""

from __future__ import annotations

import os


PROVIDER_CAPABILITIES: dict[str, dict] = {
	"minimax": {
		"roles": ["lead", "extract", "summarize"],
		"api_key_env": "MINIMAX_API_KEY",
		"models": ["MiniMax-M2.5", "MiniMax-M2.1", "MiniMax-M2"],
	},
	"opencode_go": {
		"roles": ["lead", "extract", "summarize"],
		"api_key_env": "OPENCODE_API_KEY",
		"models": ["minimax-m2.7", "minimax-m2.5", "kimi-k2.5", "glm-5"],
	},
	"zai": {
		"roles": ["lead", "extract", "summarize"],
		"api_key_env": "ZAI_API_KEY",
		"models": ["glm-4.7", "glm-4.5-air", "glm-4.5"],
	},
	"openai": {
		"roles": ["lead", "extract", "summarize"],
		"api_key_env": "OPENAI_API_KEY",
	},
	"openrouter": {
		"roles": ["lead", "extract", "summarize"],
		"api_key_env": "OPENROUTER_API_KEY",
	},
	"ollama": {
		"roles": ["lead", "extract", "summarize"],
		"api_key_env": None,
	},
	"mlx": {
		"roles": ["lead"],
		"api_key_env": None,
	},
}


def validate_provider_for_role(provider: str, role: str, model: str = "") -> None:
	"""Raise RuntimeError with helpful message if provider+model doesn't support the role."""
	provider = provider.strip().lower()
	caps = PROVIDER_CAPABILITIES.get(provider)
	if caps is None:
		supported = ", ".join(sorted(PROVIDER_CAPABILITIES.keys()))
		raise RuntimeError(
			f"Unknown provider '{provider}'. Supported providers: {supported}"
		)
	if role not in caps["roles"]:
		supported_roles = ", ".join(caps["roles"])
		raise RuntimeError(
			f"Provider '{provider}' does not support role '{role}'. "
			f"Supported roles for {provider}: {supported_roles}"
		)


def get_missing_api_key_message(provider: str) -> str | None:
	"""Return error message if the provider's required API key is not set, else None."""
	provider = provider.strip().lower()
	caps = PROVIDER_CAPABILITIES.get(provider, {})
	env_var = caps.get("api_key_env")
	if env_var and not os.environ.get(env_var):
		return f"Set {env_var} in your .env file to use provider '{provider}'"
	return None


def normalize_model_name(provider: str, model: str) -> str:
	"""Return the canonical model name for a provider.

	Performs case-insensitive matching against the provider's known model list.
	Returns the correctly-cased name if found, otherwise returns the input
	unchanged (for unknown models or providers with open-ended model lists).
	"""
	caps = PROVIDER_CAPABILITIES.get(provider.strip().lower(), {})
	known = caps.get("models")
	if not known:
		return model
	lookup = {m.lower(): m for m in known}
	return lookup.get(model.strip().lower(), model)
