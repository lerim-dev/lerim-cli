"""Provider builders for PydanticAI orchestration and DSPy pipelines."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import dspy
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.models.openrouter import OpenRouterModel, OpenRouterModelSettings
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.providers.openrouter import OpenRouterProvider

from lerim.config.settings import Config, DSPyRoleConfig, LLMRoleConfig, get_config

RoleName = Literal["lead", "explorer"]
DSPyRoleName = Literal["extract", "summarize"]


@dataclass(frozen=True)
class FallbackSpec:
    """Parsed fallback descriptor used for model-chain construction."""

    provider: str
    model: str


def _role_config(config: Config, role: RoleName) -> LLMRoleConfig:
    """Return orchestration role config from runtime config."""
    if role == "lead":
        return config.lead_role
    return config.explorer_role


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


def _build_single_orchestration_model(
    *,
    provider: str,
    model: str,
    api_base: str,
    config: Config,
    openrouter_provider_order: tuple[str, ...] = (),
):
    """Build one PydanticAI model object for a provider/model pair."""
    provider_name = provider.strip().lower()
    if provider_name == "openrouter":
        provider_obj = OpenRouterProvider(
            api_key=_api_key_for_provider(config, "openrouter")
        )
        settings = None
        if openrouter_provider_order:
            settings = OpenRouterModelSettings(
                openrouter_provider={"order": list(openrouter_provider_order)}
            )
        return OpenRouterModel(
            model_name=model, provider=provider_obj, settings=settings
        )
    if provider_name == "ollama":
        ollama_base = api_base or _default_api_base("ollama", config)
        provider_obj = OpenAIProvider(
            api_key="ollama",
            base_url=f"{ollama_base}/v1"
            if not ollama_base.endswith("/v1")
            else ollama_base,
        )
        return OpenAIChatModel(model_name=model, provider=provider_obj)
    if provider_name in {"zai", "openai"}:
        provider_obj = OpenAIProvider(
            api_key=_api_key_for_provider(config, provider_name),
            base_url=api_base or _default_api_base(provider_name),
        )
        return OpenAIChatModel(model_name=model, provider=provider_obj)
    raise RuntimeError(f"unsupported_orchestration_provider:{provider_name}")


def build_orchestration_model(
    role: RoleName,
    *,
    config: Config | None = None,
):
    """Build PydanticAI model (with optional fallback chain) for one role."""
    cfg = config or get_config()
    role_cfg = _role_config(cfg, role)
    return build_orchestration_model_from_role(role_cfg, config=cfg)


def build_orchestration_model_from_role(
    role_cfg: LLMRoleConfig,
    *,
    config: Config | None = None,
):
    """Build PydanticAI model chain from an explicit role config object."""
    cfg = config or get_config()
    primary = _build_single_orchestration_model(
        provider=role_cfg.provider,
        model=role_cfg.model,
        api_base=role_cfg.api_base,
        config=cfg,
        openrouter_provider_order=role_cfg.openrouter_provider_order,
    )
    fallback_specs = [parse_fallback_spec(item) for item in role_cfg.fallback_models]
    if not fallback_specs:
        return primary
    fallback_models = [
        _build_single_orchestration_model(
            provider=item.provider,
            model=item.model,
            api_base="",
            config=cfg,
            openrouter_provider_order=role_cfg.openrouter_provider_order,
        )
        for item in fallback_specs
    ]
    return FallbackModel(primary, *fallback_models)


def _build_dspy_lm_for_provider(
    *,
    provider: str,
    model: str,
    api_base: str,
    cfg: Config,
    role_label: str,
    openrouter_provider_order: tuple[str, ...] = (),
) -> dspy.LM:
    """Build a single DSPy LM object from provider/model/api_base."""
    if provider == "ollama":
        return dspy.LM(
            f"ollama_chat/{model}",
            api_key="ollama",
            api_base=api_base or _default_api_base("ollama"),
            cache=False,
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
            extra_body=extra_body,
        )
    if provider in {"zai", "openai"}:
        api_key = _api_key_for_provider(cfg, provider)
        env_name = "ZAI_API_KEY" if provider == "zai" else "OPENAI_API_KEY"
        if not api_key:
            raise RuntimeError(f"missing_api_key:{env_name} required for {role_label}")
        return dspy.LM(
            f"openai/{model}",
            api_key=api_key,
            api_base=api_base or _default_api_base(provider),
            cache=False,
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
    )


def build_dspy_sub_lm(
    role: DSPyRoleName,
    *,
    config: Config | None = None,
) -> dspy.LM:
    """Build a DSPy sub-LM for RLM sub-calls (llm_query/llm_query_batched)."""
    cfg = config or get_config()
    role_cfg = _dspy_role_config(cfg, role)
    return _build_dspy_lm_for_provider(
        provider=role_cfg.sub_provider.strip().lower(),
        model=role_cfg.sub_model,
        api_base=role_cfg.api_base,
        cfg=cfg,
        role_label=f"roles.{role}.sub_provider={role_cfg.sub_provider}",
        openrouter_provider_order=role_cfg.openrouter_provider_order,
    )


def list_provider_models(provider: str) -> list[str]:
    """Return static provider model suggestions for dashboard UI selections."""
    normalized = str(provider).strip().lower()
    options = {
        "zai": ["glm-4.5-air"],
        "openrouter": [
            "qwen/qwen3-coder-30b-a3b-instruct",
            "anthropic/claude-sonnet-4-5-20250929",
            "anthropic/claude-haiku-4-5-20251001",
        ],
        "openai": ["gpt-5-mini", "gpt-5"],
        "ollama": ["qwen3:8b", "qwen3:4b", "qwen3:14b"],
    }
    return list(options.get(normalized, []))


if __name__ == "__main__":
    """Run provider-layer self-test for fallback and DSPy builders."""
    cfg = get_config()

    lead_model = build_orchestration_model("lead", config=cfg)
    assert lead_model is not None

    lead_cfg = cfg.lead_role
    if lead_cfg.fallback_models:
        assert isinstance(lead_model, FallbackModel)

    dspy_model = build_dspy_lm("extract", config=cfg)
    assert isinstance(dspy_model, dspy.LM)

    print(
        f"""\
providers: \
lead={cfg.lead_role.provider}/{cfg.lead_role.model} \
fallbacks={len(cfg.lead_role.fallback_models)} \
extract={cfg.extract_role.provider}/{cfg.extract_role.model}"""
    )
