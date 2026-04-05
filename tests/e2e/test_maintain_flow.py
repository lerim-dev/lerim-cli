"""E2E tests for the maintain flow -- memory maintenance on seeded data.

Gate: LERIM_E2E=1. Real LLM calls via LerimRuntime.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lerim.config.settings import get_config
from lerim.server.runtime import LerimRuntime


@pytest.mark.timeout(300)
def test_maintain_full_flow(seeded_memory):
	"""Maintain on seeded memory completes without crash and returns valid result."""
	config = get_config()
	runtime = LerimRuntime(config=config, default_cwd=str(seeded_memory))
	memory_root = seeded_memory / "memory"
	workspace = seeded_memory / "workspace"

	result = runtime.maintain(
		memory_root=str(memory_root),
		workspace_root=str(workspace),
	)

	# Contract fields present.
	assert result["run_folder"]
	assert result["memory_root"]
	assert result["workspace_root"]
	assert result["cost_usd"] >= 0


@pytest.mark.timeout(300)
def test_maintain_writes_artifacts(seeded_memory):
	"""After maintain, the run folder contains agent.log."""
	config = get_config()
	runtime = LerimRuntime(config=config, default_cwd=str(seeded_memory))
	memory_root = seeded_memory / "memory"
	workspace = seeded_memory / "workspace"

	result = runtime.maintain(
		memory_root=str(memory_root),
		workspace_root=str(workspace),
	)

	run_folder = Path(result["run_folder"])
	assert run_folder.exists()

	agent_log = run_folder / "agent.log"
	assert agent_log.exists(), "agent.log must be written after maintain"
	assert len(agent_log.read_text(encoding="utf-8").strip()) > 0
