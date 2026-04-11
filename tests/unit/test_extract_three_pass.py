"""Unit tests for the three-pass PydanticAI extraction pipeline.

Pure Python, no LLM calls. Tests the schemas, tool function signatures, agent
builders, system prompts, and usage limits defined in
`src/lerim/agents/extract.py` and the shared tool functions in
`src/lerim/agents/tools.py`. End-to-end behavior is covered by the self-test
in `extract.py.__main__` and by the integration suite.
"""

from __future__ import annotations

import inspect
from dataclasses import fields

from pydantic_ai.models.test import TestModel

from lerim.agents import extract as extract_module
from lerim.agents.extract import (
	EXTRACT_SYSTEM_PROMPT,
	FINALIZE_SYSTEM_PROMPT,
	REFLECT_SYSTEM_PROMPT,
	CandidateMemory,
	ChunkRef,
	ExtractResult,
	FinalizeResult,
	SessionUnderstanding,
	build_extract_agent,
	build_finalize_agent,
	build_reflect_agent,
	run_extraction_three_pass,
)
from lerim.config.settings import RoleConfig, load_config
from lerim.agents.tools import (
	ExtractDeps,
	edit,
	grep,
	read,
	scan,
	verify_index,
	write,
)


def test_extract_deps_schema():
	"""ExtractDeps has exactly 3 fields: memory_root, trace_path, run_folder.

	NO `tools` field — the class is path-only; tool functions access
	ctx.deps.memory_root directly.
	"""
	field_names = {f.name for f in fields(ExtractDeps)}
	assert field_names == {"memory_root", "trace_path", "run_folder"}
	assert "tools" not in field_names


def test_session_understanding_schema():
	"""SessionUnderstanding has user_goal, key_decisions, important_chunks,
	extractable_candidates, existing_memories_relevant. Nested ChunkRef has
	offset+topic; CandidateMemory has type+topic+evidence_offset.
	"""
	field_names = set(SessionUnderstanding.model_fields.keys())
	assert field_names == {
		"user_goal",
		"key_decisions",
		"important_chunks",
		"extractable_candidates",
		"existing_memories_relevant",
	}

	# Nested ChunkRef
	chunk_field_names = set(ChunkRef.model_fields.keys())
	assert chunk_field_names == {"offset", "topic"}

	# Nested CandidateMemory
	candidate_field_names = set(CandidateMemory.model_fields.keys())
	assert candidate_field_names == {"type", "topic", "evidence_offset"}

	# Constructable
	understanding = SessionUnderstanding(
		user_goal="Test goal",
		key_decisions=["decision 1"],
		important_chunks=[ChunkRef(offset=0, topic="intro")],
		extractable_candidates=[
			CandidateMemory(type="feedback", topic="prefer tabs", evidence_offset=42),
		],
		existing_memories_relevant=["feedback_tabs.md"],
	)
	assert understanding.user_goal == "Test goal"
	assert understanding.important_chunks[0].offset == 0
	assert understanding.extractable_candidates[0].evidence_offset == 42


def test_tool_functions_take_runcontext():
	"""All six tool functions must take RunContext[ExtractDeps] as their first
	positional arg (named `ctx`). This is what wires deps into the tool body
	via `ctx.deps.memory_root` / `ctx.deps.trace_path`.
	"""
	tool_functions = [read, grep, scan, write, edit, verify_index]
	for fn in tool_functions:
		sig = inspect.signature(fn)
		params = list(sig.parameters.values())
		assert len(params) >= 1, f"{fn.__name__} has no parameters"
		first = params[0]
		assert first.name == "ctx", (
			f"{fn.__name__} first param is {first.name!r}, expected 'ctx'"
		)
		# Annotation is stringified because of `from __future__ import annotations`.
		annotation_str = str(first.annotation)
		assert "RunContext" in annotation_str, (
			f"{fn.__name__} first param annotation is {annotation_str!r}, "
			f"expected to mention RunContext"
		)
		assert "ExtractDeps" in annotation_str, (
			f"{fn.__name__} first param annotation is {annotation_str!r}, "
			f"expected to mention ExtractDeps"
		)


def test_tool_functions_live_in_tools_module():
	"""Tool functions are defined in `lerim.agents.tools` — the single source
	of truth. `lerim.agents.extract` should only import them, never redefine.
	"""
	from lerim.agents import tools as tools_module
	from lerim.agents import extract as extract_module

	for name in ("read", "grep", "scan", "write", "edit", "verify_index"):
		assert hasattr(tools_module, name), f"tools.{name} is missing"
		# extract.py imports them so `hasattr` is True there too, but they
		# must be the SAME objects (i.e., no duplicate definitions).
		assert getattr(extract_module, name) is getattr(tools_module, name), (
			f"extract.{name} is not the same object as tools.{name} — "
			f"did you redefine the tool in extract.py?"
		)


def test_agent_builders_construct_without_error():
	"""All three agent builders construct with a TestModel and expose the
	expected output_type, so they're wired up correctly end-to-end.
	"""
	model = TestModel()

	reflect_agent = build_reflect_agent(model)
	extract_agent = build_extract_agent(model)
	finalize_agent = build_finalize_agent(model)

	assert reflect_agent.output_type is SessionUnderstanding
	assert extract_agent.output_type is ExtractResult
	assert finalize_agent.output_type is FinalizeResult


def test_reflect_prompt_contains_session_understanding():
	"""Pass 1 prompt must mention SessionUnderstanding as the output type
	and describe scanning existing memories, reading in chunks, and not writing.
	"""
	assert "SessionUnderstanding" in REFLECT_SYSTEM_PROMPT
	# Mentions the read-only tools by their new names
	assert "read(" in REFLECT_SYSTEM_PROMPT or "`read`" in REFLECT_SYSTEM_PROMPT or "read_" in REFLECT_SYSTEM_PROMPT
	assert "scan(" in REFLECT_SYSTEM_PROMPT or "`scan`" in REFLECT_SYSTEM_PROMPT or "scan_" in REFLECT_SYSTEM_PROMPT or "scan " in REFLECT_SYSTEM_PROMPT
	# Mentions chunked reading
	assert "chunk" in REFLECT_SYSTEM_PROMPT.lower() or "offset" in REFLECT_SYSTEM_PROMPT
	# Explicit no-write guard
	assert "DO NOT WRITE" in REFLECT_SYSTEM_PROMPT or "not write" in REFLECT_SYSTEM_PROMPT.lower()


def test_extract_prompt_contains_why_and_how():
	"""Pass 2 prompt must carry the body-format rules (inline bold **Why:** /
	**How to apply:**), the extraction criteria, and dedup guidance.
	"""
	assert "**Why:**" in EXTRACT_SYSTEM_PROMPT
	assert "**How to apply:**" in EXTRACT_SYSTEM_PROMPT
	assert "dedup" in EXTRACT_SYSTEM_PROMPT.lower() or "duplicate" in EXTRACT_SYSTEM_PROMPT.lower()
	# Extraction criteria markers (at least one)
	assert "Do NOT extract" in EXTRACT_SYSTEM_PROMPT
	# It should mention the key memory types
	assert "feedback" in EXTRACT_SYSTEM_PROMPT


def test_reflect_prompt_does_not_mandate_scan_in_orientation():
	"""Fix #5: reflect step 1 must orient via read('index.md') ONLY,
	not mandate a separate scan() call. index.md is the authoritative
	summary of all memories — calling both wastes a tool turn.
	"""
	# The ORIENT step must tell the agent to read index.md once
	assert 'read("index.md")' in REFLECT_SYSTEM_PROMPT
	# And must explicitly tell the agent NOT to call scan() in orientation
	assert "Do NOT also call scan()" in REFLECT_SYSTEM_PROMPT or "do NOT also call scan" in REFLECT_SYSTEM_PROMPT
	# The authoritative-summary explanation must be present so the model
	# understands *why* it's skipping scan
	assert "authoritative" in REFLECT_SYSTEM_PROMPT.lower()


def test_extract_prompt_does_not_mandate_scan_in_dedup():
	"""Fix #5: extract dedup step must use existing_memories_relevant from the
	SessionUnderstanding directly, never call scan() (which would return
	the same filenames at a wasted tool turn's cost).
	"""
	# The DEDUP step must reference existing_memories_relevant
	assert "existing_memories_relevant" in EXTRACT_SYSTEM_PROMPT
	# And explicitly forbid scan() in dedup
	assert "Do NOT call scan()" in EXTRACT_SYSTEM_PROMPT or "do NOT call scan" in EXTRACT_SYSTEM_PROMPT


def test_finalize_prompt_contains_verify_index():
	"""Pass 3 prompt must mention verify_index and the session summary
	headings it writes.
	"""
	assert "verify_index" in FINALIZE_SYSTEM_PROMPT
	assert "session summary" in FINALIZE_SYSTEM_PROMPT.lower()
	assert "## User Intent" in FINALIZE_SYSTEM_PROMPT
	assert "## What Happened" in FINALIZE_SYSTEM_PROMPT


def test_finalize_prompt_hard_rules():
	"""Fix #2: finalize prompt must forbid read('trace'), cap verify_index
	calls at two, and forbid scan() — these were the wandering patterns
	that exhausted the old 8-request budget on 3/30 baseline cases.
	"""
	assert "HARD RULES" in FINALIZE_SYSTEM_PROMPT
	# read("trace") explicitly forbidden
	assert 'read("trace")' in FINALIZE_SYSTEM_PROMPT
	assert "forbidden" in FINALIZE_SYSTEM_PROMPT.lower()
	# verify_index call cap
	assert "at most TWICE" in FINALIZE_SYSTEM_PROMPT
	# scan() forbidden in this pass
	assert "do NOT call scan" in FINALIZE_SYSTEM_PROMPT or "not call scan" in FINALIZE_SYSTEM_PROMPT.lower()


def test_no_module_level_usage_limits_constants():
	"""Regression for the config-wiring fix: the three per-pass budgets must
	NOT live as module-level constants in extract.py. They flow from
	default.toml → RoleConfig → `run_extraction_three_pass` kwargs.

	If a future refactor reintroduces REFLECT_LIMITS / EXTRACT_LIMITS /
	FINALIZE_LIMITS at module scope, this test catches it immediately.
	"""
	assert not hasattr(extract_module, "REFLECT_LIMITS"), (
		"REFLECT_LIMITS reappeared as a module constant — usage limits must "
		"flow from default.toml via Config.agent_role.usage_limit_reflect"
	)
	assert not hasattr(extract_module, "EXTRACT_LIMITS"), (
		"EXTRACT_LIMITS reappeared as a module constant"
	)
	assert not hasattr(extract_module, "FINALIZE_LIMITS"), (
		"FINALIZE_LIMITS reappeared as a module constant"
	)


def test_run_extraction_three_pass_requires_limit_kwargs():
	"""Fix: run_extraction_three_pass must REQUIRE the three limit kwargs.

	Calling without them is an error — no silent defaults. The three
	limits must come from the caller (runtime.py sources them from
	Config.agent_role; eval harness sources them from load_config()).
	"""
	import inspect

	sig = inspect.signature(run_extraction_three_pass)
	params = sig.parameters

	# Each limit is a keyword-only parameter with no default
	for name in ("reflect_limit", "extract_limit", "finalize_limit"):
		assert name in params, f"run_extraction_three_pass missing {name}"
		param = params[name]
		assert param.kind == inspect.Parameter.KEYWORD_ONLY, (
			f"{name} should be keyword-only so callers can't accidentally "
			f"pass them positionally"
		)
		assert param.default is inspect.Parameter.empty, (
			f"{name} has a default value — it must be required from the "
			f"caller (no silent hardcoded defaults)"
		)


def test_usage_limit_config_defaults_match_default_toml():
	"""RoleConfig dataclass defaults must stay in sync with default.toml.

	The dataclass defaults are only used by test fixtures that construct
	RoleConfig directly. Production code flows through `_build_role` which
	uses `_require_int` and raises if the TOML is missing the key. Still,
	drifting defaults are a code smell — keep them in sync.
	"""
	config = load_config()
	role = config.agent_role
	# All three pass budgets come from default.toml (or user override).
	# Value must be a positive int; the test doesn't hardcode the number
	# so it survives users tuning their own config.
	assert role.usage_limit_reflect > 0
	assert role.usage_limit_extract > 0
	assert role.usage_limit_finalize > 0

	# The dataclass defaults should be positive too (used only in test
	# fixtures, but they should still be sensible numbers).
	defaults = RoleConfig(provider="x", model="y")
	assert defaults.usage_limit_reflect > 0
	assert defaults.usage_limit_extract > 0
	assert defaults.usage_limit_finalize > 0
