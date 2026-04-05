"""Integration tests for maintenance quality -- real LLM calls.

Gate: LERIM_INTEGRATION=1. Uses retry_on_llm_flake for non-deterministic output.
Each test seeds memory with fixture files and runs MaintainAgent, then asserts
the memory store was correctly consolidated, pruned, or organized.
"""

from __future__ import annotations

from pathlib import Path

import dspy
import pytest

from lerim.agents.maintain import MaintainAgent
from lerim.agents.tools import MemoryTools
from lerim.config.providers import build_dspy_lm
from lerim.config.settings import get_config
from tests.integration.conftest import retry_on_llm_flake

FIXTURES_DIR = Path(__file__).parents[1] / "fixtures"
MEMORIES_DIR = FIXTURES_DIR / "memories"


def _memory_files(memory_root: Path) -> list[Path]:
	"""Return all .md files in memory_root except index.md."""
	return [f for f in sorted(memory_root.glob("*.md")) if f.name != "index.md"]


def _archived_files(memory_root: Path) -> list[Path]:
	"""Return all .md files in the archived subdirectory."""
	archived_dir = memory_root / "archived"
	if not archived_dir.is_dir():
		return []
	return sorted(archived_dir.glob("*.md"))


def _seed_files(memory_root: Path, filenames: list[str]) -> None:
	"""Copy specific fixture memory files into memory_root and build index.md."""
	for name in filenames:
		src = MEMORIES_DIR / name
		(memory_root / name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

	# Build a minimal index.md referencing seeded files
	import frontmatter as fm_lib
	lines = ["# Memory Index\n"]
	for name in filenames:
		post = fm_lib.load(str(MEMORIES_DIR / name))
		title = post.get("name", name)
		desc = post.get("description", "")
		lines.append(f"- [{title}]({name}) -- {desc}")
	(memory_root / "index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


@retry_on_llm_flake(max_attempts=3)
@pytest.mark.timeout(180)
def test_maintain_merges_near_duplicates(tmp_lerim_root):
	"""Near-duplicate memories should be merged or one archived after maintain."""
	config = get_config()
	lm = build_dspy_lm("lead", config=config)
	memory_root = tmp_lerim_root / "memory"
	_seed_files(memory_root, [
		"learning_duplicate_a.md",
		"learning_duplicate_b.md",
	])

	count_before = len(_memory_files(memory_root))
	assert count_before == 2

	agent = MaintainAgent(memory_root=memory_root, max_iters=30)
	with dspy.context(lm=lm, adapter=dspy.XMLAdapter()):
		agent.forward()

	count_after = len(_memory_files(memory_root))
	archived = _archived_files(memory_root)

	# Either files were archived (reducing active count) or merged into fewer
	assert count_after < count_before or len(archived) >= 1, (
		f"Expected merge/archive of near-duplicates: "
		f"before={count_before}, after={count_after}, archived={len(archived)}"
	)


@retry_on_llm_flake(max_attempts=3)
@pytest.mark.timeout(180)
def test_maintain_archives_stale(tmp_lerim_root):
	"""Stale/outdated memory (CSS IE11 hack) should be archived or flagged."""
	config = get_config()
	lm = build_dspy_lm("lead", config=config)
	memory_root = tmp_lerim_root / "memory"
	_seed_files(memory_root, ["learning_stale.md"])

	agent = MaintainAgent(memory_root=memory_root, max_iters=30)
	with dspy.context(lm=lm, adapter=dspy.XMLAdapter()):
		agent.forward()

	stale_path = memory_root / "learning_stale.md"
	archived = _archived_files(memory_root)
	archived_names = {f.name for f in archived}

	# Either the file was archived or its body was updated with a staleness note
	if "learning_stale.md" in archived_names:
		return  # archived -- pass

	if stale_path.exists():
		content = stale_path.read_text(encoding="utf-8").lower()
		has_stale_note = any(
			marker in content
			for marker in ("stale", "outdated", "deprecated", "no longer", "obsolete", "ie11")
		)
		assert has_stale_note, (
			"learning_stale.md was neither archived nor annotated as stale"
		)
		return

	# File gone from both active and archived -- also acceptable (agent may have
	# written a replacement and archived the original under a different name)
	assert len(archived) >= 1, (
		"learning_stale.md disappeared without being archived"
	)


@retry_on_llm_flake(max_attempts=3)
@pytest.mark.timeout(180)
def test_maintain_fixes_index(tmp_lerim_root):
	"""Maintain should fix a broken index.md so verify_index returns OK."""
	config = get_config()
	lm = build_dspy_lm("lead", config=config)
	memory_root = tmp_lerim_root / "memory"

	# Seed memories
	for src in MEMORIES_DIR.glob("*.md"):
		(memory_root / src.name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

	# Write a deliberately broken index.md -- missing most entries
	(memory_root / "index.md").write_text(
		"# Memory Index\n\n"
		"## References\n"
		"- [Stale CSS hack](learning_stale.md) -- CSS IE11 fallback\n",
		encoding="utf-8",
	)

	# Confirm index is broken before maintain
	tools_before = MemoryTools(memory_root=memory_root)
	pre_check = tools_before.verify_index()
	assert pre_check.startswith("NOT OK"), (
		f"Index should be broken before maintain, got: {pre_check}"
	)

	agent = MaintainAgent(memory_root=memory_root, max_iters=30)
	with dspy.context(lm=lm, adapter=dspy.XMLAdapter()):
		agent.forward()

	tools_after = MemoryTools(memory_root=memory_root)
	post_check = tools_after.verify_index()
	assert post_check.startswith("OK"), (
		f"verify_index should return OK after maintain, got: {post_check}"
	)


@retry_on_llm_flake(max_attempts=3)
@pytest.mark.timeout(180)
def test_maintain_preserves_summaries(tmp_lerim_root):
	"""Maintain must not modify or archive summary files."""
	config = get_config()
	lm = build_dspy_lm("lead", config=config)
	memory_root = tmp_lerim_root / "memory"

	# Seed memories
	_seed_files(memory_root, [
		"decision_auth_pattern.md",
		"learning_queue_fix.md",
	])

	# Create a summary file in summaries/
	summaries_dir = memory_root / "summaries"
	summaries_dir.mkdir(parents=True, exist_ok=True)
	summary_content = (
		"---\n"
		"name: Auth setup session\n"
		"description: Set up JWT authentication for the API\n"
		"type: summary\n"
		"---\n"
		"\n"
		"## User Intent\n"
		"\n"
		"Set up authentication for the API service.\n"
		"\n"
		"## What Happened\n"
		"\n"
		"Implemented JWT with HS256 signing. Added middleware and tests.\n"
	)
	summary_path = summaries_dir / "20260401_120000_auth_setup.md"
	summary_path.write_text(summary_content, encoding="utf-8")

	agent = MaintainAgent(memory_root=memory_root, max_iters=30)
	with dspy.context(lm=lm, adapter=dspy.XMLAdapter()):
		agent.forward()

	# Summary file must still exist with identical content
	assert summary_path.exists(), (
		f"Summary file {summary_path.name} was deleted or moved by maintain"
	)
	assert summary_path.read_text(encoding="utf-8") == summary_content, (
		f"Summary file {summary_path.name} was modified by maintain"
	)
