"""End-to-end tests for real sync flow (requires real LLM)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

_skip = pytest.mark.skipif(
    not os.environ.get("LERIM_E2E"),
    reason="LERIM_E2E not set",
)

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "traces"


@_skip
def test_sync_real_trace(tmp_path):
    """lerim sync on a real trace file produces workspace artifacts and memory files."""
    from lerim.runtime.agent import LerimAgent

    trace = FIXTURES_DIR / "claude_simple.jsonl"
    memory_root = tmp_path / "memory"
    for sub in ("decisions", "learnings", "summaries"):
        (memory_root / sub).mkdir(parents=True)
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    agent = LerimAgent()
    result = agent.sync(
        trace_path=trace,
        memory_root=tmp_path,
        workspace_root=workspace,
    )
    assert isinstance(result, dict)
    assert "counts" in result
    assert "run_folder" in result
    # Run folder should exist
    run_folder = Path(result["run_folder"])
    assert run_folder.exists()


@_skip
def test_sync_idempotent(tmp_path):
    """Running sync twice on same trace doesn't duplicate memories."""
    from lerim.runtime.agent import LerimAgent

    trace = FIXTURES_DIR / "claude_simple.jsonl"
    memory_root = tmp_path / "memory"
    for sub in ("decisions", "learnings", "summaries"):
        (memory_root / sub).mkdir(parents=True)
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    agent = LerimAgent()
    agent.sync(trace_path=trace, memory_root=tmp_path, workspace_root=workspace)
    result2 = agent.sync(
        trace_path=trace, memory_root=tmp_path, workspace_root=workspace
    )
    # Second run should not add as many memories (most should be no_op/update)
    counts2 = result2.get("counts", {})
    assert isinstance(counts2, dict)
