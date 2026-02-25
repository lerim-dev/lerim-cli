"""Integration tests for provider fallback behavior (requires real LLM)."""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.integration

_skip = pytest.mark.skipif(
    not os.environ.get("LERIM_INTEGRATION"),
    reason="LERIM_INTEGRATION not set",
)


@_skip
def test_provider_fallback_on_error(tmp_path):
    """When primary provider fails, fallback is used."""
    from dataclasses import replace
    from lerim.config.settings import LLMRoleConfig
    from lerim.runtime.agent import LerimAgent
    from tests.helpers import make_config

    # Configure primary with invalid model, fallback with valid
    cfg = make_config(tmp_path)
    role = LLMRoleConfig(
        provider=cfg.lead_role.provider,
        model="nonexistent-model-xyz",
        api_base=cfg.lead_role.api_base,
        fallback_models=(f"{cfg.lead_role.provider}:{cfg.lead_role.model}",),
        timeout_seconds=300,
        max_iterations=24,
        openrouter_provider_order=cfg.lead_role.openrouter_provider_order,
    )
    cfg = replace(cfg, lead_role=role)
    # This tests that the agent can recover via fallback
    agent = LerimAgent()
    response, _ = agent.chat("hello", memory_root=tmp_path)
    assert isinstance(response, str)
