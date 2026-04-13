"""E2E tests for the sync flow -- full trace to memories pipeline.

Gate: LERIM_E2E=1. Real LLM calls via LerimRuntime.
"""

from __future__ import annotations

from pathlib import Path

import frontmatter
import pytest

from lerim.config.settings import get_config
from lerim.server.runtime import LerimRuntime

TRACES_DIR = Path(__file__).parents[1] / "fixtures" / "traces"


@pytest.mark.timeout(300)
def test_sync_full_flow(tmp_lerim_root):
	"""Sync a short trace and verify memories and index.md are created on disk."""
	config = get_config()
	runtime = LerimRuntime(config=config, default_cwd=str(tmp_lerim_root))
	memory_root = tmp_lerim_root / "memory"
	workspace = tmp_lerim_root / "workspace"
	trace = TRACES_DIR / "claude_short.jsonl"

	result = runtime.sync(
		trace_path=str(trace),
		memory_root=str(memory_root),
		workspace_root=str(workspace),
	)

	# Contract fields present.
	assert result["run_folder"]
	assert result["trace_path"]
	assert result["memory_root"]
	assert result["workspace_root"]
	assert result["cost_usd"] >= 0

	# Memories written to disk (excluding index.md).
	memories = [f for f in memory_root.rglob("*.md") if f.name != "index.md"]
	assert len(memories) >= 1, "sync should create at least one memory file"

	# index.md exists and has content.
	index = memory_root / "index.md"
	assert index.exists(), "index.md must exist after sync"
	assert len(index.read_text(encoding="utf-8").strip()) > 0


@pytest.mark.timeout(300)
def test_sync_writes_artifacts(tmp_lerim_root):
	"""After sync, the run folder contains agent.log and agent_trace.json."""
	config = get_config()
	runtime = LerimRuntime(config=config, default_cwd=str(tmp_lerim_root))
	memory_root = tmp_lerim_root / "memory"
	workspace = tmp_lerim_root / "workspace"
	trace = TRACES_DIR / "claude_short.jsonl"

	result = runtime.sync(
		trace_path=str(trace),
		memory_root=str(memory_root),
		workspace_root=str(workspace),
	)

	run_folder = Path(result["run_folder"])
	assert run_folder.exists()

	agent_log = run_folder / "agent.log"
	assert agent_log.exists(), "agent.log must be written after sync"
	assert len(agent_log.read_text(encoding="utf-8").strip()) > 0

	agent_trace = run_folder / "agent_trace.json"
	assert agent_trace.exists(), "agent_trace.json must be written after sync"


@pytest.mark.timeout(600)
def test_sync_idempotency(tmp_lerim_root):
	"""Running sync twice should avoid duplicate active memories."""
	config = get_config()
	runtime = LerimRuntime(config=config, default_cwd=str(tmp_lerim_root))
	memory_root = tmp_lerim_root / "memory"
	workspace = tmp_lerim_root / "workspace"
	trace = TRACES_DIR / "claude_short.jsonl"

	# First sync.
	runtime.sync(
		trace_path=str(trace),
		memory_root=str(memory_root),
		workspace_root=str(workspace),
	)
	first_active = [f for f in memory_root.glob("*.md") if f.name != "index.md"]
	first_count = len(first_active)

	# Second sync on the same trace and memory root.
	runtime.sync(
		trace_path=str(trace),
		memory_root=str(memory_root),
		workspace_root=str(workspace),
	)
	second_active = [f for f in memory_root.glob("*.md") if f.name != "index.md"]
	second_count = len(second_active)

	# The extractor can legitimately add up to 3 active memories in a run.
	assert second_count <= first_count + 3, (
		f"unexpected active-memory growth across repeated sync: "
		f"first={first_count}, second={second_count}"
	)

	# Guard against duplicate memory descriptions after two runs.
	descriptions: list[str] = []
	for path in second_active:
		post = frontmatter.load(str(path))
		desc = str(post.get("description", "")).strip().lower()
		if desc:
			descriptions.append(desc)
	assert len(descriptions) == len(set(descriptions)), (
		"duplicate memory descriptions detected after repeated sync"
	)
