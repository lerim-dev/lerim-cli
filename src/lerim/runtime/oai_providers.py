"""Provider builders for OpenAI Agents SDK using LitellmModel.

Maps lerim's provider config (openrouter, minimax, zai, openai, ollama, mlx)
to LitellmModel instances compatible with the agents SDK Runner.
"""

from __future__ import annotations

from agents.extensions.models.litellm_model import LitellmModel

from lerim.config.settings import Config, LLMRoleConfig, get_config
from lerim.runtime.provider_caps import validate_provider_for_role
from lerim.runtime.providers import (
	_api_key_for_provider,
	_default_api_base,
	_role_config,
	parse_fallback_spec,
	RoleName,
)


def _build_litellm_model(
	*,
	provider: str,
	model: str,
	api_base: str,
	config: Config,
) -> LitellmModel:
	"""Build one LitellmModel for a provider/model pair."""
	provider_name = provider.strip().lower()

	if provider_name == "openrouter":
		return LitellmModel(
			model=f"openrouter/{model}",
			api_key=_api_key_for_provider(config, "openrouter"),
		)

	if provider_name == "minimax":
		return LitellmModel(
			model=f"minimax/{model}",
			api_key=_api_key_for_provider(config, "minimax"),
		)

	if provider_name == "ollama":
		base = api_base or _default_api_base("ollama", config)
		return LitellmModel(
			model=f"ollama_chat/{model}",
			api_key="ollama",
			base_url=f"{base}/v1" if base and not base.endswith("/v1") else base,
		)

	if provider_name == "mlx":
		base = api_base or _default_api_base("mlx", config)
		return LitellmModel(
			model=f"openai/{model}",
			api_key="mlx",
			base_url=f"{base}/v1" if base and not base.endswith("/v1") else base,
		)

	if provider_name == "opencode_go":
		api_key = _api_key_for_provider(config, "opencode_go")
		base = api_base or _default_api_base("opencode_go", config)
		# All Go models work on the Chat Completions endpoint
		return LitellmModel(model=f"openai/{model}", api_key=api_key, base_url=base or None)

	if provider_name in {"zai", "openai"}:
		api_key = _api_key_for_provider(config, provider_name)
		base = api_base or _default_api_base(provider_name, config)
		return LitellmModel(
			model=f"openai/{model}",
			api_key=api_key,
			base_url=base or None,
		)

	raise RuntimeError(f"unsupported_oai_provider:{provider_name}")


def build_oai_model(
	role: RoleName,
	*,
	config: Config | None = None,
) -> LitellmModel:
	"""Build primary LitellmModel for an orchestration role."""
	cfg = config or get_config()
	role_cfg = _role_config(cfg, role)
	return build_oai_model_from_role(role_cfg, config=cfg)


def build_oai_model_from_role(
	role_cfg: LLMRoleConfig,
	*,
	config: Config | None = None,
) -> LitellmModel:
	"""Build primary LitellmModel from an explicit role config."""
	cfg = config or get_config()
	return _build_litellm_model(
		provider=role_cfg.provider,
		model=role_cfg.model,
		api_base=role_cfg.api_base,
		config=cfg,
	)


def build_oai_fallback_models(
	role_cfg: LLMRoleConfig,
	*,
	config: Config | None = None,
) -> list[LitellmModel]:
	"""Build fallback LitellmModels from role config's fallback_models list."""
	cfg = config or get_config()
	specs = [parse_fallback_spec(item) for item in role_cfg.fallback_models]
	return [
		_build_litellm_model(
			provider=spec.provider,
			model=spec.model,
			api_base="",
			config=cfg,
		)
		for spec in specs
	]


def build_codex_options(
	*,
	config: Config | None = None,
	role: RoleName = "lead",
	codex_provider: str | None = None,
	codex_model: str | None = None,
) -> tuple[dict, dict, bool]:
	"""Build CodexOptions and ThreadOptions kwargs for codex_tool() from lerim config.

	The Codex CLI requires the Responses API wire format. Providers that support it
	natively (OpenAI, OpenRouter) are used directly. For other providers (MiniMax,
	ZAI, Ollama, MLX, OpenCode Go), a local ResponsesProxy is needed to translate
	Responses API → Chat Completions.

	When codex_provider/codex_model are given they override the lead role's
	provider and model for the Codex sub-agent, allowing lead and codex to
	use different providers.

	Returns:
		(codex_options_kwargs, thread_options_kwargs, needs_proxy)
		- needs_proxy=True means the caller must start a ResponsesProxy and set
		  codex_options_kwargs["base_url"] to the proxy URL before use.
		- When needs_proxy=True, codex_options_kwargs also contains "backend_url"
		  and "backend_api_key" for constructing the proxy.
	"""
	cfg = config or get_config()
	role_cfg = _role_config(cfg, role)

	# Use explicit codex provider/model if provided, otherwise fall back to lead role.
	provider = (codex_provider or role_cfg.provider).strip().lower()
	model = codex_model or role_cfg.model

	# Validate the provider can serve the codex role.
	validate_provider_for_role(provider, "codex", model)

	thread_kwargs = {
		"model": model,
		"approval_policy": "never",
		"network_access_enabled": False,
	}

	# Providers with native Responses API support — use directly.
	if provider == "openrouter":
		codex_kwargs = {
			"base_url": _default_api_base("openrouter", cfg) or "https://openrouter.ai/api/v1",
			"api_key": _api_key_for_provider(cfg, "openrouter"),
		}
		return codex_kwargs, thread_kwargs, False

	if provider == "openai":
		codex_kwargs = {
			"base_url": _default_api_base("openai", cfg) or None,
			"api_key": _api_key_for_provider(cfg, "openai"),
		}
		return codex_kwargs, thread_kwargs, False

	# Providers that need the local ResponsesProxy (Chat Completions only).
	api_key = _api_key_for_provider(cfg, provider) or ""
	backend_url = _default_api_base(provider, cfg)

	if not backend_url:
		defaults = {
			"minimax": "https://api.minimax.io/v1",
			"zai": "https://open.bigmodel.cn/api/paas/v4",
			"ollama": "http://127.0.0.1:11434/v1",
			"mlx": "http://127.0.0.1:8000/v1",
			"opencode_go": "https://opencode.ai/zen/go/v1",
		}
		backend_url = defaults.get(provider, "")

	codex_kwargs = {
		# base_url will be set to proxy.url by the caller after starting the proxy
		"base_url": None,
		"api_key": "proxy-managed",
		# Extra fields for the caller to construct the proxy
		"backend_url": backend_url,
		"backend_api_key": api_key,
	}
	return codex_kwargs, thread_kwargs, True


if __name__ == "__main__":
	"""Self-test: build lead model, fallback models, and codex config."""
	cfg = get_config()

	lead_model = build_oai_model("lead", config=cfg)
	assert lead_model is not None
	assert isinstance(lead_model, LitellmModel)

	lead_cfg = cfg.lead_role
	fallbacks = build_oai_fallback_models(lead_cfg, config=cfg)
	assert isinstance(fallbacks, list)

	codex_opts, thread_opts, needs_proxy = build_codex_options(config=cfg)
	assert "model" in thread_opts

	print(
		f"oai_providers: "
		f"lead={cfg.lead_role.provider}/{cfg.lead_role.model} "
		f"fallbacks={len(fallbacks)} "
		f"codex_model={thread_opts['model']} "
		f"needs_proxy={needs_proxy}"
	)
