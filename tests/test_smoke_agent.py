"""Smoke tests for agent chat (requires ollama)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.smoke

_skip = pytest.mark.skipif(
    not os.environ.get("LERIM_SMOKE"),
    reason="LERIM_SMOKE not set",
)


@_skip
def test_agent_chat_responds(tmp_path):
    """LerimAgent.chat() returns non-empty string for simple query."""
    from lerim.runtime.agent import LerimAgent

    # Seed memory with one fixture file
    memory_dir = tmp_path / "memory" / "decisions"
    memory_dir.mkdir(parents=True)
    (memory_dir / "auth.md").write_text(
        "---\nid: auth\ntitle: Use JWT\ntags: [auth]\n---\nJWT with HS256.",
        encoding="utf-8",
    )
    agent = LerimAgent()
    response, session_id = agent.chat(
        "What decisions have been made?",
        memory_root=tmp_path,
    )
    assert isinstance(response, str)
    assert len(response) > 0
