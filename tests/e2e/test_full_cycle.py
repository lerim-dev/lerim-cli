"""End-to-end test for full reset -> sync -> chat cycle (requires real LLM)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

_skip = pytest.mark.skipif(
    not os.environ.get("LERIM_E2E"),
    reason="LERIM_E2E not set",
)

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "traces"


@_skip
def test_reset_sync_chat_cycle(tmp_path):
    """Full cycle: reset -> sync -> chat returns relevant response."""
    from lerim.memory.memory_repo import build_memory_paths, reset_memory_root
    from lerim.runtime.agent import LerimAgent

    # Setup — build proper MemoryPaths from data root
    paths = build_memory_paths(tmp_path)

    # Reset (creates canonical directory structure)
    reset_memory_root(paths)

    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)

    # Sync
    trace = FIXTURES_DIR / "claude_simple.jsonl"
    agent = LerimAgent()
    sync_result = agent.sync(
        trace_path=trace,
        memory_root=tmp_path,
        workspace_root=workspace,
    )
    assert isinstance(sync_result, dict)

    # Chat
    response, _ = agent.chat(
        "What was discussed about authentication?",
        memory_root=tmp_path,
    )
    assert isinstance(response, str)
    assert len(response) > 0
