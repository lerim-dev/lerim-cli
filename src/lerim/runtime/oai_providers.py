"""Provider builders for OpenAI Agents SDK using LitellmModel.

Maps lerim's provider config (openrouter, minimax, zai, openai, ollama, mlx)
to LitellmModel instances compatible with the agents SDK Runner.
"""

from __future__ import annotations

from agents.extensions.models.litellm_model import LitellmModel

from lerim.config.settings import Config, LLMRoleConfig, get_config
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


if __name__ == "__main__":
	"""Self-test: build lead model and fallback models."""
	cfg = get_config()

	lead_model = build_oai_model("lead", config=cfg)
	assert lead_model is not None
	assert isinstance(lead_model, LitellmModel)

	lead_cfg = cfg.lead_role
	fallbacks = build_oai_fallback_models(lead_cfg, config=cfg)
	assert isinstance(fallbacks, list)

	print(
		f"oai_providers: "
		f"lead={cfg.lead_role.provider}/{cfg.lead_role.model} "
		f"fallbacks={len(fallbacks)}"
	)
