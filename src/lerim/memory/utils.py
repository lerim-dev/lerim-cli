"""Shared DSPy helpers used by memory extraction and trace summarization pipelines."""

from __future__ import annotations

import dspy
from lerim.config.logging import logger
from lerim.config.settings import get_config
from lerim.runtime.providers import build_dspy_lm


def configure_dspy_lm(role: str = "extract") -> dspy.LM:
    """Configure DSPy LM for a role using runtime provider builders."""
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
    return build_dspy_lm(normalized_role, config=config, configure_global=True)


if __name__ == "__main__":
    """Run direct smoke check for config-based DSPy setup."""
    config = get_config()
    assert config.extract_role.provider
    assert config.extract_role.model
    assert config.summarize_role.provider
    assert config.summarize_role.model
    print(
        f"""\
DSPy config: \
extract={config.extract_role.provider}/{config.extract_role.model}, \
summarize={config.summarize_role.provider}/{config.summarize_role.model}"""
    )
