"""Integration tests for extraction quality -- real LLM calls.

Gate: LERIM_INTEGRATION=1. Uses retry_on_llm_flake for non-deterministic output.
Each test runs the ExtractAgent against fixture traces and asserts quality
properties of the resulting memory files, summaries, and index.
"""

from __future__ import annotations

from pathlib import Path

import dspy
import frontmatter
import pytest

from lerim.agents.extract import ExtractAgent
from lerim.agents.tools import MemoryTools
from lerim.config.providers import build_dspy_lm
from lerim.config.settings import get_config
from tests.integration.conftest import retry_on_llm_flake

FIXTURES_DIR = Path(__file__).parents[1] / "fixtures"
TRACES_DIR = FIXTURES_DIR / "traces"


def _memory_files(memory_root: Path) -> list[Path]:
	"""Return all .md files in memory_root except index.md."""
	return [f for f in sorted(memory_root.glob("*.md")) if f.name != "index.md"]


def _summary_files(memory_root: Path) -> list[Path]:
	"""Return all .md files in the summaries subdirectory."""
	summaries_dir = memory_root / "summaries"
	if not summaries_dir.is_dir():
		return []
	return sorted(summaries_dir.glob("*.md"))


@retry_on_llm_flake(max_attempts=3)
@pytest.mark.timeout(180)
def test_extract_body_has_why_and_how(tmp_lerim_root):
	"""Feedback/project memories must contain Why and How to apply sections."""
	config = get_config()
	lm = build_dspy_lm("lead", config=config)
	memory_root = tmp_lerim_root / "memory"
	(memory_root / "index.md").write_text("# Memory Index\n")
	trace = TRACES_DIR / "claude_short.jsonl"

	agent = ExtractAgent(memory_root=memory_root, trace_path=trace, max_iters=15)
	with dspy.context(lm=lm, adapter=dspy.XMLAdapter()):
		agent.forward()

	checked = 0
	for md_file in _memory_files(memory_root):
		post = frontmatter.load(str(md_file))
		mem_type = post.get("type", "")
		if mem_type in ("feedback", "project"):
			body = post.content
			assert "**Why:**" in body, (
				f"{md_file.name} (type={mem_type}) missing **Why:** section"
			)
			assert "**How to apply:**" in body, (
				f"{md_file.name} (type={mem_type}) missing **How to apply:** section"
			)
			checked += 1

	# The trace has extractable content -- at least 1 feedback/project expected
	assert checked >= 1, "Expected at least 1 feedback/project memory with body sections"


@retry_on_llm_flake(max_attempts=3)
@pytest.mark.timeout(180)
def test_extract_dedup_does_not_duplicate(tmp_lerim_root):
	"""Running extraction twice on the same trace must not create duplicates."""
	config = get_config()
	lm = build_dspy_lm("lead", config=config)
	memory_root = tmp_lerim_root / "memory"
	(memory_root / "index.md").write_text("# Memory Index\n")
	trace = TRACES_DIR / "claude_short.jsonl"

	# First extraction
	agent1 = ExtractAgent(memory_root=memory_root, trace_path=trace, max_iters=15)
	with dspy.context(lm=lm, adapter=dspy.XMLAdapter()):
		agent1.forward()

	count_after_first = len(_memory_files(memory_root))
	assert count_after_first >= 1, "First extraction should produce at least 1 memory"

	# Second extraction on same trace, same memory_root
	agent2 = ExtractAgent(memory_root=memory_root, trace_path=trace, max_iters=15)
	with dspy.context(lm=lm, adapter=dspy.XMLAdapter()):
		agent2.forward()

	count_after_second = len(_memory_files(memory_root))
	assert count_after_second == count_after_first, (
		f"Second extraction added {count_after_second - count_after_first} new files; "
		f"expected 0 (dedup should prevent duplicates)"
	)


@retry_on_llm_flake(max_attempts=3)
@pytest.mark.timeout(180)
def test_extract_respects_do_not_extract(tmp_lerim_root):
	"""Trivial/empty traces should produce 0 memory files."""
	config = get_config()
	lm = build_dspy_lm("lead", config=config)
	memory_root = tmp_lerim_root / "memory"
	(memory_root / "index.md").write_text("# Memory Index\n")
	trace = TRACES_DIR / "edge_short.jsonl"

	agent = ExtractAgent(memory_root=memory_root, trace_path=trace, max_iters=15)
	with dspy.context(lm=lm, adapter=dspy.XMLAdapter()):
		agent.forward()

	memories = _memory_files(memory_root)
	assert len(memories) == 0, (
		f"Trivial trace should produce 0 memories, got {len(memories)}: "
		f"{[f.name for f in memories]}"
	)


@retry_on_llm_flake(max_attempts=3)
@pytest.mark.timeout(180)
def test_extract_summary_has_sections(tmp_lerim_root):
	"""Session summary must contain User Intent and What Happened sections."""
	config = get_config()
	lm = build_dspy_lm("lead", config=config)
	memory_root = tmp_lerim_root / "memory"
	(memory_root / "index.md").write_text("# Memory Index\n")
	trace = TRACES_DIR / "claude_short.jsonl"

	agent = ExtractAgent(memory_root=memory_root, trace_path=trace, max_iters=15)
	with dspy.context(lm=lm, adapter=dspy.XMLAdapter()):
		agent.forward()

	summaries = _summary_files(memory_root)
	assert len(summaries) >= 1, "Extraction should produce at least 1 summary file"

	for summary_path in summaries:
		content = summary_path.read_text(encoding="utf-8")
		assert "## User Intent" in content, (
			f"Summary {summary_path.name} missing '## User Intent' section"
		)
		assert "## What Happened" in content, (
			f"Summary {summary_path.name} missing '## What Happened' section"
		)


@retry_on_llm_flake(max_attempts=3)
@pytest.mark.timeout(180)
def test_extract_index_has_all_files(tmp_lerim_root):
	"""After extraction, verify_index must return OK."""
	config = get_config()
	lm = build_dspy_lm("lead", config=config)
	memory_root = tmp_lerim_root / "memory"
	(memory_root / "index.md").write_text("# Memory Index\n")
	trace = TRACES_DIR / "claude_short.jsonl"

	agent = ExtractAgent(memory_root=memory_root, trace_path=trace, max_iters=15)
	with dspy.context(lm=lm, adapter=dspy.XMLAdapter()):
		agent.forward()

	tools = MemoryTools(memory_root=memory_root)
	result = tools.verify_index()
	assert result.startswith("OK"), (
		f"verify_index should return OK after extraction, got: {result}"
	)
