"""Provider builders for DSPy and PydanticAI pipelines and shared utilities.

Single source of truth for role → model construction. Includes:
- Provider capability registry (env var names, known models)
- Model name normalization per provider
- Role validation
- DSPy LM builders (used by maintain/ask agents)
- PydanticAI Model builders with HTTP retry + provider fallback (used by
  the extract three-pass pipeline and the single-pass baseline)

Provider base URLs are read from the `[providers]` section of `default.toml`
(+ optional `~/.lerim/config.toml` override). API keys are resolved from
environment variables via `Config`. Nothing is hardcoded in this module
beyond the capability registry (which is a facts-about-providers table,
not configuration).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import dspy
from httpx import AsyncClient, HTTPStatusError
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError
from pydantic_ai.models import Model
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.retries import AsyncTenacityTransport, RetryConfig, wait_retry_after
from tenacity import retry_if_exception_type, stop_after_attempt, wait_exponential

from lerim.config.settings import Config, RoleConfig, get_config

DSPyRoleName = Literal["agent"]
PydanticAIRoleName = Literal["agent"]


# ---------------------------------------------------------------------------
# Provider capability registry and validation
# ---------------------------------------------------------------------------

PROVIDER_CAPABILITIES: dict[str, dict] = {
	"minimax": {
		"roles": ["agent"],
		"api_key_env": "MINIMAX_API_KEY",
		"models": ["MiniMax-M2.5", "MiniMax-M2.1", "MiniMax-M2"],
	},
	"opencode_go": {
		"roles": ["agent"],
		"api_key_env": "OPENCODE_API_KEY",
		"models": ["minimax-m2.7", "minimax-m2.5", "kimi-k2.5", "glm-5"],
	},
	"zai": {
		"roles": ["agent"],
		"api_key_env": "ZAI_API_KEY",
		"models": ["glm-4.7", "glm-4.5-air", "glm-4.5"],
	},
	"openai": {
		"roles": ["agent"],
		"api_key_env": "OPENAI_API_KEY",
	},
	"openrouter": {
		"roles": ["agent"],
		"api_key_env": "OPENROUTER_API_KEY",
	},
	"ollama": {
		"roles": ["agent"],
		"api_key_env": None,
	},
	"mlx": {
		"roles": ["agent"],
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


@dataclass(frozen=True)
class FallbackSpec:
	"""Parsed fallback descriptor used for model-chain construction."""

	provider: str
	model: str


def _dspy_role_config(config: Config, role: DSPyRoleName) -> RoleConfig:
	"""Return role config for DSPy LM construction."""
	return config.agent_role


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
		model = normalize_model_name(default_provider, text)
		return FallbackSpec(provider=default_provider, model=model)
	provider, model = text.split(":", 1)
	provider = provider.strip().lower()
	model = model.strip()
	if not provider or not model:
		raise RuntimeError(f"fallback_model_invalid:{raw}")
	model = normalize_model_name(provider, model)
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
	temperature: float = 1.0,
	max_tokens: int = 32000,
) -> dspy.LM:
	"""Build a single DSPy LM object from provider/model/api_base."""
	if provider == "ollama":
		kwargs: dict = dict(
			api_key="ollama",
			api_base=api_base or _default_api_base("ollama"),
			temperature=temperature,
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
			temperature=temperature,
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
			temperature=temperature,
			cache=False,
			max_tokens=max_tokens,
			extra_body=extra_body,
		)
	if provider == "opencode_go":
		api_key = _api_key_for_provider(cfg, "opencode_go")
		if not api_key:
			raise RuntimeError(f"missing_api_key:OPENCODE_API_KEY required for {role_label}")
		base = api_base or _default_api_base("opencode_go")
		return dspy.LM(f"openai/{model}", api_key=api_key, api_base=base, temperature=temperature, cache=False, max_tokens=max_tokens)
	if provider in {"zai", "openai", "minimax"}:
		api_key = _api_key_for_provider(cfg, provider)
		env_name = {
			"zai": "ZAI_API_KEY",
			"openai": "OPENAI_API_KEY",
			"minimax": "MINIMAX_API_KEY",
		}[provider]
		if not api_key:
			raise RuntimeError(f"missing_api_key:{env_name} required for {role_label}")
		# Use native litellm prefix when available (enables function calling support).
		litellm_prefix = {"minimax": "minimax", "zai": "zai", "openai": "openai"}[provider]
		return dspy.LM(
			f"{litellm_prefix}/{model}",
			api_key=api_key,
			api_base=api_base or _default_api_base(provider),
			temperature=temperature,
			cache=False,
			max_tokens=max_tokens,
		)
	raise RuntimeError(f"unsupported_dspy_provider:{provider}")


def build_dspy_lm(
	role: DSPyRoleName,
	*,
	config: Config | None = None,
) -> dspy.LM:
	"""Build a DSPy LM object for the agent role.

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
		temperature=role_cfg.temperature,
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
			temperature=role_cfg.temperature,
			max_tokens=role_cfg.max_tokens,
		)
		for spec in specs
	]


# ---------------------------------------------------------------------------
# PydanticAI model builders (extract agents use these)
# ---------------------------------------------------------------------------


def _make_retrying_http_client(
	max_attempts: int = 5,
	max_wait_seconds: int = 120,
) -> AsyncClient:
	"""Build an httpx AsyncClient with tenacity retries for transient errors.

	Retries individual HTTP requests on 429 (honoring Retry-After header),
	5xx server errors, and network errors at the transport layer —
	transparent to the agent loop. A failed model request retries in-place
	instead of crashing the enclosing agent run.

	Does NOT retry on 400 (bad request — won't change), 401/403 (auth),
	or other client errors. Those propagate as ModelHTTPError so a
	FallbackModel wrapper can switch providers.
	"""
	def _validate_response(response):
		# Raise HTTPStatusError for retryable status codes so tenacity picks it up.
		if response.status_code in (429, 500, 502, 503, 504):
			response.raise_for_status()

	transport = AsyncTenacityTransport(
		config=RetryConfig(
			retry=retry_if_exception_type(HTTPStatusError),
			wait=wait_retry_after(
				fallback_strategy=wait_exponential(multiplier=2, min=1, max=60),
				max_wait=max_wait_seconds,
			),
			stop=stop_after_attempt(max_attempts),
			reraise=True,
		),
		validate_response=_validate_response,
	)
	return AsyncClient(transport=transport)


def _build_model_settings(provider: str, cfg: Config) -> OpenAIChatModelSettings:
	"""Build PydanticAI model settings from Lerim Config.agent_role.

	Threads ``temperature`` and ``max_tokens`` from ``default.toml`` into
	the PydanticAI path (previously dead fields — only DSPy honored
	them). Also enables ``parallel_tool_calls=True`` so the model can
	emit multiple independent tool calls in a single turn (1 LLM round-
	trip instead of N), which is a big wall-clock win on write/note
	bursts where each call is logically independent.

	Provider-specific clamping:
	- MiniMax documents a valid temperature range of ``(0.0, 1.0]``.
	  Values outside this range produce errors at the API. We clamp to
	  that window specifically for minimax to prevent a config-time
	  error from leaking into a runtime agent run. Other providers pass
	  through unchanged.

	Known sensitivity: with MiniMax-M2.5, the combination of low
	temperature (e.g. 0.1) and ``retries=3`` previously caused the agent
	to crash on stochastic tool-call flub bursts because low temperature
	made the flubs deterministic. The current setup pairs config
	temperature with ``retries=5`` on the Agent + ``parallel_tool_calls=
	True`` to absorb both stochastic flubs AND collapse independent
	calls into one turn. If smoke shows regression, bump ``temperature``
	in ``default.toml`` instead of reverting this threading.

	We deliberately do NOT set ``extra_body={"reasoning_split": True}``
	for MiniMax-M2.x, even though the docs expose it, because
	pydantic_ai does not preserve the resulting ``reasoning_details``
	field in message history. Setting it would break MiniMax's chain-
	of-thought continuity across turns. The default behavior (thinking
	embedded in ``content`` as ``<think>`` tags) is correctly replayed
	by pydantic_ai verbatim.
	"""
	role_cfg = cfg.agent_role
	temperature = role_cfg.temperature
	if provider == "minimax":
		# Clamp strictly inside (0.0, 1.0] per MiniMax API docs
		temperature = max(0.01, min(1.0, temperature))
	return OpenAIChatModelSettings(
		temperature=temperature,
		max_tokens=role_cfg.max_tokens,
		parallel_tool_calls=True,
	)


def _build_pydantic_model_for_provider(
	*,
	provider: str,
	model: str,
	api_base: str,
	cfg: Config,
	role_label: str,
) -> OpenAIChatModel:
	"""Build a single PydanticAI OpenAI-compatible model with HTTP retry.

	Uses the shared Config to resolve API keys, base URLs, AND model
	settings (temperature, max_tokens) — no hardcoded endpoints. Every
	provider Lerim supports has an OpenAI-compatible API, so
	``OpenAIChatModel`` with a ``OpenAIProvider`` works universally.
	"""
	provider = provider.strip().lower()
	validate_provider_for_role(provider, "agent", model)

	if provider == "ollama":
		api_key = "ollama"
	else:
		api_key = _api_key_for_provider(cfg, provider)
		if not api_key:
			caps = PROVIDER_CAPABILITIES.get(provider, {})
			env_name = caps.get("api_key_env", "<unknown>")
			raise RuntimeError(
				f"missing_api_key:{env_name} required for {role_label}"
			)

	base_url = api_base or _default_api_base(provider, cfg)
	if not base_url:
		raise RuntimeError(
			f"missing_api_base:no default base URL configured for "
			f"provider={provider} (set [providers].{provider} in default.toml)"
		)

	http_client = _make_retrying_http_client()
	openai_provider = OpenAIProvider(
		base_url=base_url,
		api_key=api_key,
		http_client=http_client,
	)
	canonical_model = normalize_model_name(provider, model)
	settings = _build_model_settings(provider, cfg)
	return OpenAIChatModel(canonical_model, provider=openai_provider, settings=settings)


def _wrap_with_fallback(
	primary: OpenAIChatModel,
	fallbacks: list[OpenAIChatModel],
) -> Model:
	"""Return primary alone if no fallbacks, else a FallbackModel wrapping both."""
	if not fallbacks:
		return primary
	return FallbackModel(
		primary,
		*fallbacks,
		fallback_on=(ModelHTTPError, ModelAPIError),
	)


def build_pydantic_model(
	role: PydanticAIRoleName = "agent",
	*,
	config: Config | None = None,
) -> Model:
	"""Build a robust PydanticAI model for the given role from Config.

	Reads provider/model/fallbacks from `Config` (from `default.toml` +
	`~/.lerim/config.toml`) — this is the runtime-side builder used by
	`LerimRuntime.sync()` and by agent `__main__` self-tests. For the eval
	harness, where each eval cell specifies its own provider/model in an
	eval TOML, use `build_pydantic_model_from_provider` instead.

	Returns a `FallbackModel` wrapping:

	- Primary: the role's configured provider/model with HTTP-level retry
	  (`AsyncTenacityTransport` — handles 429/5xx/network in place)
	- Fallbacks: every entry in `role.fallback_models` (e.g. `"zai:glm-4.7"`),
	  each with its own retry transport

	The FallbackModel switches to the next model when the current one
	raises `ModelHTTPError` or `ModelAPIError` — without restarting the
	agent run. If no fallback models are configured, returns the bare
	retrying primary model (which still has HTTP retry).
	"""
	cfg = config or get_config()
	role_cfg = _dspy_role_config(cfg, role)

	primary = _build_pydantic_model_for_provider(
		provider=role_cfg.provider,
		model=role_cfg.model,
		api_base=role_cfg.api_base,
		cfg=cfg,
		role_label=f"roles.{role}.provider={role_cfg.provider}",
	)

	fallbacks: list[OpenAIChatModel] = []
	for raw in role_cfg.fallback_models:
		try:
			spec = parse_fallback_spec(raw)
			fallbacks.append(
				_build_pydantic_model_for_provider(
					provider=spec.provider,
					model=spec.model,
					api_base="",
					cfg=cfg,
					role_label=f"roles.{role}.fallback={spec.provider}:{spec.model}",
				)
			)
		except RuntimeError:
			# Missing API key for the fallback — skip it silently. The
			# primary's HTTP retries still protect against transient errors.
			continue

	return _wrap_with_fallback(primary, fallbacks)


def build_pydantic_model_from_provider(
	provider: str,
	model: str,
	*,
	fallback_models: tuple[str, ...] | list[str] | None = None,
	config: Config | None = None,
) -> Model:
	"""Build a robust PydanticAI model from explicit provider/model args.

	Used by the eval harness (each eval TOML cell specifies its own
	provider/model/fallbacks that override the default Lerim Config) and
	by the eval judge (which may use a different model than the agent role).

	Unlike `build_pydantic_model`, this does NOT read provider/model from
	Lerim's `Config.agent_role`. It still uses `Config` to resolve API keys
	from environment variables and base URLs from `[providers]`, so the
	config file stays the single source of truth for endpoints and keys.

	Args:
		provider: Provider name (e.g. "minimax", "zai", "openai", "ollama").
		model: Model name for the provider.
		fallback_models: Optional sequence of `"provider:model"` strings
			(same format as `default.toml` `fallback_models`). None means
			no fallback — just the primary with HTTP-level retry.
		config: Optional Config override (defaults to `get_config()`).

	Returns:
		FallbackModel if fallbacks are configured and their API keys
		are available, else a bare retrying OpenAIChatModel primary.
	"""
	cfg = config or get_config()

	primary = _build_pydantic_model_for_provider(
		provider=provider,
		model=model,
		api_base="",
		cfg=cfg,
		role_label=f"explicit_provider={provider}",
	)

	fallbacks: list[OpenAIChatModel] = []
	for raw in fallback_models or ():
		try:
			spec = parse_fallback_spec(raw)
			fallbacks.append(
				_build_pydantic_model_for_provider(
					provider=spec.provider,
					model=spec.model,
					api_base="",
					cfg=cfg,
					role_label=f"explicit_fallback={spec.provider}:{spec.model}",
				)
			)
		except RuntimeError:
			continue

	return _wrap_with_fallback(primary, fallbacks)


def list_provider_models(provider: str) -> list[str]:
	"""Return static provider model suggestions for dashboard UI selections."""
	normalized = str(provider).strip().lower()
	caps = PROVIDER_CAPABILITIES.get(normalized, {})
	if "models" in caps:
		return list(caps["models"])
	# Open-ended providers: curated suggestions only
	extras: dict[str, list[str]] = {
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
	}
	return list(extras.get(normalized, []))


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
	dspy_model = build_dspy_lm("agent", config=cfg)
	assert isinstance(dspy_model, dspy.LM)

	print(
		f"""\
providers: \
agent={cfg.agent_role.provider}/{cfg.agent_role.model}"""
	)
