"""End-to-end tests for real maintain flow (requires real LLM)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

_skip = pytest.mark.skipif(
    not os.environ.get("LERIM_E2E"),
    reason="LERIM_E2E not set",
)

MEMORIES_DIR = Path(__file__).parent.parent / "fixtures" / "memories"


@_skip
def test_maintain_on_seeded_memory(tmp_path):
    """lerim maintain on memory with duplicates produces maintain actions."""
    from lerim.runtime.agent import LerimAgent

    # Seed memory with duplicate fixtures
    decisions = tmp_path / "memory" / "decisions"
    learnings = tmp_path / "memory" / "learnings"
    for sub in (
        "decisions",
        "learnings",
        "summaries",
        "archived/decisions",
        "archived/learnings",
    ):
        (tmp_path / "memory" / sub).mkdir(parents=True, exist_ok=True)
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    for src in MEMORIES_DIR.glob("decision_*.md"):
        (decisions / src.name).write_text(src.read_text(), encoding="utf-8")
    for src in MEMORIES_DIR.glob("learning_*.md"):
        (learnings / src.name).write_text(src.read_text(), encoding="utf-8")

    agent = LerimAgent()
    result = agent.maintain(memory_root=tmp_path, workspace_root=workspace)
    assert isinstance(result, dict)
    assert "counts" in result
    assert "run_folder" in result
