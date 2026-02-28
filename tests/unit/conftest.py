"""Unit test fixtures — autouse dummy API key so PydanticAI provider constructors work."""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _ensure_api_key(monkeypatch):
    """Set a dummy OPENROUTER_API_KEY for unit tests that construct LerimAgent.

    The PydanticAI provider constructor requires an API key even when the
    actual LLM call is monkeypatched.  Real keys are only needed for
    smoke/integration/e2e tests.
    """
    if not os.environ.get("OPENROUTER_API_KEY"):
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-dummy-key")
