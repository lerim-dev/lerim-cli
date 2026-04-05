"""Smoke tests for Lerim agents — real LLM round-trips.

Gate: LERIM_SMOKE=1. Uses minimax (or LERIM_TEST_PROVIDER override).
Each test should complete in <60s.
"""

from __future__ import annotations

from pathlib import Path

import dspy
import frontmatter as fm_lib
import pytest

from lerim.agents.ask import AskAgent
from lerim.agents.extract import ExtractAgent
from lerim.agents.maintain import MaintainAgent
from lerim.agents.tools import MEMORY_TYPES
from lerim.config.providers import build_dspy_lm
from lerim.config.settings import get_config

TRACES_DIR = Path(__file__).parent.parent / "fixtures" / "traces"
TRACE_PATH = TRACES_DIR / "claude_short.jsonl"


@pytest.fixture
def lead_lm():
	"""Build the lead LM from test config."""
	config = get_config()
	return build_dspy_lm("lead", config=config)


@pytest.fixture
def memory_root(tmp_lerim_root):
	"""Empty memory root with index.md pre-created."""
	mem = tmp_lerim_root / "memory"
	(mem / "index.md").write_text("# Memory Index\n")
	(mem / "summaries").mkdir(exist_ok=True)
	return mem


@pytest.fixture
def seeded_memory_root(seeded_memory):
	"""Seeded memory root with index.md pre-created."""
	mem = seeded_memory / "memory"
	if not (mem / "index.md").exists():
		(mem / "index.md").write_text("# Memory Index\n")
	(mem / "summaries").mkdir(exist_ok=True)
	return mem


def _memory_files(memory_root: Path) -> list[Path]:
	"""Return non-index .md files in memory_root."""
	return [f for f in memory_root.glob("*.md") if f.name != "index.md"]


# ── Extract tests ──────────────────────────────────────────────────


@pytest.mark.smoke
@pytest.mark.timeout(120)
def test_extract_runs_and_produces_memory(memory_root, lead_lm):
	"""ExtractAgent creates at least one memory file from a short trace."""
	agent = ExtractAgent(
		memory_root=memory_root,
		trace_path=TRACE_PATH,
		max_iters=10,
	)
	with dspy.context(lm=lead_lm, adapter=dspy.XMLAdapter()):
		prediction = agent.forward()

	assert prediction.completion_summary
	assert isinstance(prediction.completion_summary, str)
	assert len(_memory_files(memory_root)) >= 1


@pytest.mark.smoke
@pytest.mark.timeout(120)
def test_extract_produces_valid_frontmatter(memory_root, lead_lm):
	"""Extracted memory files have valid 3-field frontmatter."""
	agent = ExtractAgent(
		memory_root=memory_root,
		trace_path=TRACE_PATH,
		max_iters=10,
	)
	with dspy.context(lm=lead_lm, adapter=dspy.XMLAdapter()):
		agent.forward()

	files = _memory_files(memory_root)
	assert len(files) >= 1, "Expected at least one memory file"

	for f in files:
		post = fm_lib.load(str(f))
		assert "name" in post.metadata, f"{f.name}: missing 'name' in frontmatter"
		assert "description" in post.metadata, f"{f.name}: missing 'description'"
		assert "type" in post.metadata, f"{f.name}: missing 'type'"
		assert post.metadata["type"] in MEMORY_TYPES, (
			f"{f.name}: type '{post.metadata['type']}' not in {MEMORY_TYPES}"
		)


@pytest.mark.smoke
@pytest.mark.timeout(120)
def test_extract_writes_summary(memory_root, lead_lm):
	"""ExtractAgent writes at least one summary to summaries/."""
	agent = ExtractAgent(
		memory_root=memory_root,
		trace_path=TRACE_PATH,
		max_iters=10,
	)
	with dspy.context(lm=lead_lm, adapter=dspy.XMLAdapter()):
		agent.forward()

	summaries_dir = memory_root / "summaries"
	summary_files = list(summaries_dir.glob("*.md"))
	assert len(summary_files) >= 1, "Expected at least one summary file"


@pytest.mark.smoke
@pytest.mark.timeout(120)
def test_extract_updates_index(memory_root, lead_lm):
	"""ExtractAgent updates index.md beyond the initial stub."""
	agent = ExtractAgent(
		memory_root=memory_root,
		trace_path=TRACE_PATH,
		max_iters=10,
	)
	with dspy.context(lm=lead_lm, adapter=dspy.XMLAdapter()):
		agent.forward()

	index_path = memory_root / "index.md"
	assert index_path.exists(), "index.md should exist"
	content = index_path.read_text(encoding="utf-8")
	assert len(content) > len("# Memory Index\n"), (
		"index.md should have content beyond the initial stub"
	)


# ── Maintain tests ─────────────────────────────────────────────────


@pytest.mark.smoke
@pytest.mark.timeout(120)
def test_maintain_runs_on_seeded_store(seeded_memory_root, lead_lm):
	"""MaintainAgent completes on a seeded memory store without crashing."""
	agent = MaintainAgent(
		memory_root=seeded_memory_root,
		max_iters=10,
	)
	with dspy.context(lm=lead_lm, adapter=dspy.XMLAdapter()):
		prediction = agent.forward()

	assert prediction.completion_summary
	assert isinstance(prediction.completion_summary, str)


@pytest.mark.smoke
@pytest.mark.timeout(120)
def test_maintain_does_not_crash_on_empty(memory_root, lead_lm):
	"""MaintainAgent completes on an empty memory store without error."""
	agent = MaintainAgent(
		memory_root=memory_root,
		max_iters=10,
	)
	with dspy.context(lm=lead_lm, adapter=dspy.XMLAdapter()):
		prediction = agent.forward()

	assert prediction.completion_summary
	assert isinstance(prediction.completion_summary, str)


# ── Ask tests ──────────────────────────────────────────────────────


@pytest.mark.smoke
@pytest.mark.timeout(120)
def test_ask_answers_question(seeded_memory_root, lead_lm):
	"""AskAgent returns a substantive answer when memories exist."""
	agent = AskAgent(
		memory_root=seeded_memory_root,
		max_iters=10,
	)
	with dspy.context(lm=lead_lm, adapter=dspy.XMLAdapter()):
		prediction = agent.forward(
			question="What authentication pattern does the project use?",
			hints="",
		)

	assert prediction.answer
	assert isinstance(prediction.answer, str)
	assert len(prediction.answer) > 20, (
		f"Expected substantive answer, got: {prediction.answer!r}"
	)


@pytest.mark.smoke
@pytest.mark.timeout(120)
def test_ask_no_memories_says_so(memory_root, lead_lm):
	"""AskAgent on empty store acknowledges lack of memories."""
	agent = AskAgent(
		memory_root=memory_root,
		max_iters=10,
	)
	with dspy.context(lm=lead_lm, adapter=dspy.XMLAdapter()):
		prediction = agent.forward(
			question="What is the auth pattern?",
			hints="",
		)

	assert prediction.answer
	answer_lower = prediction.answer.lower()
	assert any(
		term in answer_lower
		for term in ("no ", "not found", "empty", "no relevant", "no memories",
					 "don't have", "do not have", "cannot find", "unable")
	), f"Expected answer to indicate no data, got: {prediction.answer!r}"
