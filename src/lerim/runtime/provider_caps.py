"""Provider capability registry and validation for lerim roles.

Each provider declares which roles it supports, whether it needs the
ResponsesProxy for Codex, and any model-level restrictions.
"""

from __future__ import annotations

import os


PROVIDER_CAPABILITIES: dict[str, dict] = {
	"minimax": {
		"roles": ["lead", "codex", "extract", "summarize"],
		"codex_needs_proxy": True,
		"codex_wire_format": "chat_completions",
		"api_key_env": "MINIMAX_API_KEY",
	},
	"opencode_go": {
		"roles": ["lead", "codex", "extract", "summarize"],
		"codex_needs_proxy": True,
		"codex_wire_format": "chat_completions",
		"api_key_env": "OPENCODE_API_KEY",
		"codex_models": ["kimi-k2.5", "glm-5"],
		"codex_blocked_models": ["minimax-m2.7", "minimax-m2.5"],
		"lead_models": ["minimax-m2.7", "minimax-m2.5", "kimi-k2.5", "glm-5"],
	},
	"zai": {
		"roles": ["lead", "codex", "extract", "summarize"],
		"codex_needs_proxy": True,
		"codex_wire_format": "chat_completions",
		"api_key_env": "ZAI_API_KEY",
	},
	"openai": {
		"roles": ["lead", "codex", "extract", "summarize"],
		"codex_needs_proxy": False,
		"api_key_env": "OPENAI_API_KEY",
	},
	"openrouter": {
		"roles": ["lead", "codex", "extract", "summarize"],
		"codex_needs_proxy": False,
		"api_key_env": "OPENROUTER_API_KEY",
	},
	"ollama": {
		"roles": ["lead", "codex", "extract", "summarize"],
		"codex_needs_proxy": True,
		"codex_wire_format": "chat_completions",
		"api_key_env": None,
	},
	"mlx": {
		"roles": ["lead", "codex"],
		"codex_needs_proxy": True,
		"codex_wire_format": "chat_completions",
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
	if role == "codex" and model:
		blocked = caps.get("codex_blocked_models", [])
		if model in blocked:
			allowed = caps.get("codex_models", [])
			raise RuntimeError(
				f"Model '{model}' on provider '{provider}' cannot be used for Codex. "
				f"It uses Anthropic Messages format which the ResponsesProxy cannot translate. "
				f"Codex-compatible models for {provider}: {', '.join(allowed)}"
			)


def get_missing_api_key_message(provider: str) -> str | None:
	"""Return error message if the provider's required API key is not set, else None."""
	provider = provider.strip().lower()
	caps = PROVIDER_CAPABILITIES.get(provider, {})
	env_var = caps.get("api_key_env")
	if env_var and not os.environ.get(env_var):
		return f"Set {env_var} in your .env file to use provider '{provider}'"
	return None


def codex_needs_proxy(provider: str) -> bool:
	"""Return whether this provider needs the ResponsesProxy for Codex CLI."""
	provider = provider.strip().lower()
	caps = PROVIDER_CAPABILITIES.get(provider, {})
	return bool(caps.get("codex_needs_proxy", True))
