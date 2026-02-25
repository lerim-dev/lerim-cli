"""Shared DSPy helpers used by memory extraction and trace summarization pipelines."""

from __future__ import annotations

import dspy
from lerim.config.logging import logger
from lerim.config.settings import get_config
from lerim.runtime.providers import build_dspy_lm, build_dspy_sub_lm


def configure_dspy_lm(role: str = "extract") -> dspy.LM:
    """Build DSPy LM for a role without global configuration.

    Returns the LM object. Callers must use dspy.context(lm=lm) for
    thread-safe execution instead of dspy.configure().
    """
    config = get_config()
    normalized_role = "summarize" if role == "summarize" else "extract"
    role_cfg = (
        config.summarize_role if normalized_role == "summarize" else config.extract_role
    )
    logger.info(
        "Configuring DSPy LM for role={} provider={} model={}",
        normalized_role,
        role_cfg.provider,
        role_cfg.model,
    )
    return build_dspy_lm(normalized_role, config=config)


def configure_dspy_sub_lm(role: str = "extract") -> dspy.LM:
    """Build a separate DSPy LM for RLM sub-calls (llm_query/llm_query_batched)."""
    config = get_config()
    normalized_role = "summarize" if role == "summarize" else "extract"
    role_cfg = (
        config.summarize_role if normalized_role == "summarize" else config.extract_role
    )
    logger.info(
        "Configuring DSPy sub-LM for role={} provider={} model={}",
        normalized_role,
        role_cfg.sub_provider,
        role_cfg.sub_model,
    )
    return build_dspy_sub_lm(normalized_role, config=config)


if __name__ == "__main__":
    """Run direct smoke check for config-based DSPy setup."""
    config = get_config()
    assert config.extract_role.provider
    assert config.extract_role.model
    assert config.extract_role.sub_provider
    assert config.extract_role.sub_model
    assert config.summarize_role.provider
    assert config.summarize_role.model
    print(
        f"""\
DSPy config: \
extract={config.extract_role.provider}/{config.extract_role.model} \
sub={config.extract_role.sub_provider}/{config.extract_role.sub_model}, \
summarize={config.summarize_role.provider}/{config.summarize_role.model} \
sub={config.summarize_role.sub_provider}/{config.summarize_role.sub_model}"""
    )
