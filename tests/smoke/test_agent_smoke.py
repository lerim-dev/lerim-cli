"""Smoke tests for Lerim maintain/ask agents — real LLM round-trips.

Gate: LERIM_SMOKE=1.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lerim.agents.ask import run_ask
from lerim.agents.maintain import run_maintain
from lerim.config.providers import build_pydantic_model
from lerim.config.settings import get_config

TRACES_DIR = Path(__file__).parent.parent / "fixtures" / "traces"
TRACE_PATH = TRACES_DIR / "claude_long_multitopic.jsonl"


@pytest.fixture
def lead_model():
	"""Build the primary PydanticAI model from active test config."""
	config = get_config()
	return build_pydantic_model("agent", config=config)


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


@pytest.mark.smoke
@pytest.mark.timeout(240)
def test_maintain_runs_on_seeded_store(seeded_memory_root, lead_model):
	"""Maintain flow should complete on a seeded memory store."""
	result = run_maintain(
		memory_root=seeded_memory_root,
		model=lead_model,
		request_limit=30,
	)
	assert result.completion_summary
	assert isinstance(result.completion_summary, str)


@pytest.mark.smoke
@pytest.mark.timeout(120)
def test_maintain_does_not_crash_on_empty(memory_root, lead_model):
	"""Maintain flow should complete on an empty memory store."""
	result = run_maintain(
		memory_root=memory_root,
		model=lead_model,
		request_limit=30,
	)
	assert result.completion_summary
	assert isinstance(result.completion_summary, str)


@pytest.mark.smoke
@pytest.mark.timeout(120)
def test_ask_answers_question(seeded_memory_root, lead_model):
	"""Ask flow should return a substantive answer when memories exist."""
	result = run_ask(
		memory_root=seeded_memory_root,
		model=lead_model,
		question="What authentication pattern does the project use?",
		hints="",
		request_limit=10,
	)
	assert result.answer
	assert isinstance(result.answer, str)
	assert len(result.answer) > 20, f"Expected substantive answer, got: {result.answer!r}"


@pytest.mark.smoke
@pytest.mark.timeout(120)
def test_ask_no_memories_says_so(memory_root, lead_model):
	"""Ask flow on empty store should acknowledge missing memories."""
	result = run_ask(
		memory_root=memory_root,
		model=lead_model,
		question="What is the auth pattern?",
		hints="",
		request_limit=10,
	)
	assert result.answer
	answer_lower = result.answer.lower()
	assert any(
		term in answer_lower
		for term in (
			"no ",
			"not found",
			"empty",
			"no relevant",
			"no memories",
			"don't have",
			"do not have",
			"cannot find",
			"unable",
		)
	), f"Expected answer to indicate no data, got: {result.answer!r}"
