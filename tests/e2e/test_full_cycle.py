"""E2E test for a full sync-then-ask cycle.

Gate: LERIM_E2E=1. Real LLM calls via LerimRuntime.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lerim.config.settings import get_config
from lerim.server.runtime import LerimRuntime

TRACES_DIR = Path(__file__).parents[1] / "fixtures" / "traces"


@pytest.mark.timeout(600)
def test_sync_then_ask(tmp_lerim_root):
	"""Sync a trace to create memories, then ask a question about the session."""
	config = get_config()
	runtime = LerimRuntime(config=config, default_cwd=str(tmp_lerim_root))
	memory_root = tmp_lerim_root / "memory"
	workspace = tmp_lerim_root / "workspace"
	trace = TRACES_DIR / "claude_short.jsonl"

	# Step 1: sync to populate memories.
	sync_result = runtime.sync(
		trace_path=str(trace),
		memory_root=str(memory_root),
		workspace_root=str(workspace),
	)
	assert sync_result["run_folder"]

	# Verify at least one memory was created.
	memories = [f for f in memory_root.rglob("*.md") if f.name != "index.md"]
	assert len(memories) >= 1, "sync must create memories before ask"

	# Step 2: ask about the synced session.
	answer, session_id, cost_usd = runtime.ask(
		prompt="What was discussed in this session?",
		memory_root=str(memory_root),
	)

	assert answer, "ask should return a non-empty answer"
	assert len(answer) > 10, "answer should be substantive"
	assert session_id, "session_id must be returned"
	assert cost_usd >= 0
