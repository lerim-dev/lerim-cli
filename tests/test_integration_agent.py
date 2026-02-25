"""Integration tests for agent chat with memory context (requires real LLM)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

_skip = pytest.mark.skipif(
    not os.environ.get("LERIM_INTEGRATION"),
    reason="LERIM_INTEGRATION not set",
)

MEMORIES_DIR = Path(__file__).parent / "fixtures" / "memories"


@_skip
def test_chat_uses_memory_context(tmp_path):
    """Chat response references seeded memory content."""
    from lerim.runtime.agent import LerimAgent

    # Seed memory with known decision about auth
    decisions = tmp_path / "memory" / "decisions"
    decisions.mkdir(parents=True)
    src = MEMORIES_DIR / "decision_auth_pattern.md"
    (decisions / src.name).write_text(src.read_text(), encoding="utf-8")

    agent = LerimAgent()
    response, _ = agent.chat(
        "What auth decisions were made?",
        memory_root=tmp_path,
    )
    assert isinstance(response, str)
    assert len(response) > 0
    # Response should reference auth-related content
    lower = response.lower()
    assert "jwt" in lower or "auth" in lower or "hs256" in lower
