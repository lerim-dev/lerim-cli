"""Integration tests for ask quality -- real LLM calls.

Gate: LERIM_INTEGRATION=1. Uses retry_on_llm_flake for non-deterministic output.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lerim.agents.ask import run_ask
from lerim.config.providers import build_pydantic_model
from lerim.config.settings import get_config
from tests.integration.conftest import retry_on_llm_flake

FIXTURES_DIR = Path(__file__).parents[1] / "fixtures"
MEMORIES_DIR = FIXTURES_DIR / "memories"


def _seed_all_memories(memory_root: Path) -> list[str]:
	"""Copy all fixture memories into memory_root and build index.md."""
	import frontmatter as fm_lib

	filenames = []
	lines = ["# Memory Index\n"]
	for src in sorted(MEMORIES_DIR.glob("*.md")):
		(memory_root / src.name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
		filenames.append(src.name)
		post = fm_lib.load(str(src))
		title = post.get("name", src.name)
		desc = post.get("description", "")
		lines.append(f"- [{title}]({src.name}) -- {desc}")

	(memory_root / "index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
	return filenames


def _model_from_config():
	"""Construct the active PydanticAI model from loaded config."""
	config = get_config()
	return build_pydantic_model("agent", config=config)


@retry_on_llm_flake(max_attempts=3)
@pytest.mark.timeout(180)
def test_ask_cites_specific_files(tmp_lerim_root):
	"""Ask answer should reference auth-related content and cite memory filenames."""
	memory_root = tmp_lerim_root / "memory"
	filenames = _seed_all_memories(memory_root)

	result = run_ask(
		memory_root=memory_root,
		model=_model_from_config(),
		question="What authentication pattern does this project use?",
		hints="",
		request_limit=30,
	)
	answer = result.answer.lower()

	has_auth_content = any(
		term in answer for term in ("auth", "jwt", "hs256", "token", "session")
	)
	assert has_auth_content, (
		f"Answer should reference authentication content, got: {result.answer[:200]}"
	)

	cited_any = any(fname in result.answer for fname in filenames)
	assert cited_any, (
		f"Answer should cite at least one memory filename from {filenames}, "
		f"got: {result.answer[:200]}"
	)


@retry_on_llm_flake(max_attempts=3)
@pytest.mark.timeout(180)
def test_ask_relevant_to_question(tmp_lerim_root):
	"""Ask about queues should reference queue memory and cite queue file."""
	memory_root = tmp_lerim_root / "memory"
	_seed_all_memories(memory_root)

	result = run_ask(
		memory_root=memory_root,
		model=_model_from_config(),
		question="What do we know about queue processing and race conditions?",
		hints="",
		request_limit=30,
	)
	answer = result.answer.lower()

	has_queue_content = any(
		term in answer
		for term in ("queue", "race condition", "atomic", "claim", "duplicate processing")
	)
	assert has_queue_content, (
		f"Answer should reference queue-related content, got: {result.answer[:200]}"
	)
	assert "learning_queue_fix.md" in result.answer, (
		f"Answer should cite learning_queue_fix.md, got: {result.answer[:200]}"
	)
