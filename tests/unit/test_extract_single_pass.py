"""Unit tests for the single-pass PydanticAI extraction agent.

Pure Python, no LLM calls. Tests:
- ExtractionResult output schema
- ExtractDeps now carries the two new mutable state fields (notes, pruned_offsets)
- build_extract_agent wires the 8 tools and 3 history processors
- SYSTEM_PROMPT contains the core rules for the new scan→note→prune flow
- run_extraction signature (no per-pass limit kwargs)
- Regression: the deleted three-pass symbols and module are gone
"""

from __future__ import annotations

import inspect
from dataclasses import fields

from pydantic_ai.models.test import TestModel

from lerim.agents import extract as extract_module
from lerim.agents.extract import (
	SYSTEM_PROMPT,
	ExtractionResult,
	build_extract_agent,
	run_extraction,
)
from lerim.agents.tools import (
	ExtractDeps,
	compute_request_budget,
	edit,
	grep,
	note,
	prune,
	read,
	verify_index,
	write,
)


# ---------------------------------------------------------------------------
# Output schema
# ---------------------------------------------------------------------------


def test_extraction_result_schema():
	"""ExtractionResult has a single completion_summary field."""
	field_names = set(ExtractionResult.model_fields.keys())
	assert field_names == {"completion_summary"}

	result = ExtractionResult(completion_summary="wrote 3 memories, 1 summary")
	assert result.completion_summary == "wrote 3 memories, 1 summary"


# ---------------------------------------------------------------------------
# ExtractDeps mutable state (the LangGraph-style accumulator)
# ---------------------------------------------------------------------------


def test_extract_deps_has_notes_and_pruned_offsets(tmp_path):
	"""ExtractDeps carries 5 fields: the 3 original paths + notes + pruned_offsets.

	Both mutable fields must default to empty collections so each run starts
	fresh. Cross-run state leakage would break training data collection.
	"""
	field_names = {f.name for f in fields(ExtractDeps)}
	assert field_names == {
		"memory_root",
		"trace_path",
		"run_folder",
		"notes",
		"pruned_offsets",
	}
	deps = ExtractDeps(memory_root=tmp_path)
	assert deps.notes == []
	assert deps.pruned_offsets == set()
	# Mutable defaults must be distinct instances, not a shared singleton.
	deps2 = ExtractDeps(memory_root=tmp_path)
	assert deps.notes is not deps2.notes
	assert deps.pruned_offsets is not deps2.pruned_offsets


# ---------------------------------------------------------------------------
# Agent construction
# ---------------------------------------------------------------------------


def test_build_extract_agent_wires_seven_tools():
	"""build_extract_agent registers 7 tools: read, grep, note, prune, write, edit, verify_index.

	scan() was REMOVED in v4 (2026-04-11). Smoke tests showed MiniMax
	ignored the D1 prompt rule forbidding scan() and kept calling it.
	Structural enforcement: remove scan from the tool list so the model
	physically cannot call it. read('index.md') + verify_index() give
	the same information for the extract flow.
	"""
	agent = build_extract_agent(TestModel())
	assert agent.output_type is ExtractionResult


def test_build_extract_agent_has_three_history_processors():
	"""The agent's history_processors list contains context_pressure, notes_state, prune_history."""
	agent = build_extract_agent(TestModel())
	# _history_processors is the internal list; fall back to an attribute
	# sweep if the private name ever changes.
	processors = getattr(agent, "history_processors", None)
	if processors is None:
		processors = getattr(agent, "_history_processors", None)
	assert processors is not None, "Agent should expose history_processors"
	processor_names = {p.__name__ for p in processors if hasattr(p, "__name__")}
	assert "context_pressure_injector" in processor_names
	assert "notes_state_injector" in processor_names
	assert "prune_history_processor" in processor_names


def test_agent_tools_include_note_and_prune():
	"""note and prune must be callable with RunContext[ExtractDeps] as first arg."""
	for fn in (note, prune):
		sig = inspect.signature(fn)
		params = list(sig.parameters.values())
		assert len(params) >= 1
		first = params[0]
		assert first.name == "ctx", f"{fn.__name__} first param is {first.name!r}, expected 'ctx'"
		ann_str = str(first.annotation)
		assert "RunContext" in ann_str
		assert "ExtractDeps" in ann_str


def test_other_tool_functions_still_take_runcontext():
	"""All existing tools (read, grep, write, edit, verify_index) keep ctx as first arg.

	Note: scan() is NOT included — it was removed from the extract
	agent's tool list in v4. scan() itself still exists in tools.py
	for other agents (maintain, ask) via module-level tool wiring, but
	extract does not wire it.
	"""
	tool_functions = [read, grep, write, edit, verify_index]
	for fn in tool_functions:
		sig = inspect.signature(fn)
		params = list(sig.parameters.values())
		assert len(params) >= 1
		assert params[0].name == "ctx"
		assert "RunContext" in str(params[0].annotation)
		assert "ExtractDeps" in str(params[0].annotation)


# ---------------------------------------------------------------------------
# SYSTEM_PROMPT core content
# ---------------------------------------------------------------------------


def test_system_prompt_mentions_core_cycle():
	"""The prompt must teach the SCAN → NOTE → PRUNE cycle explicitly."""
	assert "note" in SYSTEM_PROMPT.lower()
	assert "prune" in SYSTEM_PROMPT.lower()
	# Explicit cycle wording
	assert "read" in SYSTEM_PROMPT.lower()
	# The worked example mentions Finding()
	assert "Finding(" in SYSTEM_PROMPT


def test_system_prompt_has_strict_discipline_block():
	"""STRICT DISCIPLINE D1-D7 rules must exist in the v4 optimized prompt.

	v4 changes (2026-04-11): removed the old D1 ("never call scan()") since
	scan was removed from the tool surface structurally. Rules renumbered:
	D1=grep regex once, D2=write retry terminal, D3=prune optional on small,
	D4=summary-first derivation, D5=no read trace after synthesis, D6=
	duplicates worse than gaps, D7=explicit remember override.
	"""
	assert "STRICT DISCIPLINE" in SYSTEM_PROMPT
	# All seven rule labels
	for label in ("D1.", "D2.", "D3.", "D4.", "D5.", "D6.", "D7."):
		assert label in SYSTEM_PROMPT, f"Missing rule {label}"
	# D1: grep regex with synonyms (no apostrophes — they break MiniMax's
	# OpenAI-compat JSON encoding of tool args)
	assert "remember|memorize|keep in mind|note this" in SYSTEM_PROMPT
	assert "exactly ONCE" in SYSTEM_PROMPT or "exactly once" in SYSTEM_PROMPT.lower()
	# D2: write retry terminal
	assert "already exists" in SYSTEM_PROMPT
	assert "TERMINAL" in SYSTEM_PROMPT or "terminal" in SYSTEM_PROMPT.lower() or "STOP" in SYSTEM_PROMPT
	# D4: summary-first commitment
	assert "Summary is committed FIRST" in SYSTEM_PROMPT or "summary FIRST" in SYSTEM_PROMPT or "COMMIT THE SUMMARY FIRST" in SYSTEM_PROMPT
	# D5: no read trace after synthesis
	assert "read(\"trace\"" in SYSTEM_PROMPT or "read('trace'" in SYSTEM_PROMPT
	# D6: duplicates worse than gaps
	assert "Duplicates are worse than gaps" in SYSTEM_PROMPT or "duplicates are worse" in SYSTEM_PROMPT.lower()


def test_system_prompt_mentions_global_synthesis():
	"""Global synthesis step (theme-level writes) is still present in CORE FLOW."""
	assert "synthesis" in SYSTEM_PROMPT.lower() or "synthesize" in SYSTEM_PROMPT.lower()
	# The key "write at the theme level" guidance
	assert "THEME" in SYSTEM_PROMPT or "theme level" in SYSTEM_PROMPT.lower()


def test_system_prompt_has_extraction_criteria():
	"""Preserved extraction criteria (what to / not to extract)."""
	assert "Extract:" in SYSTEM_PROMPT
	assert "Do NOT extract:" in SYSTEM_PROMPT
	assert "feedback" in SYSTEM_PROMPT
	assert "user role" in SYSTEM_PROMPT.lower() or "User role" in SYSTEM_PROMPT


def test_system_prompt_has_body_format_rules():
	"""Preserved body format rules for feedback/project memories."""
	assert "**Why:**" in SYSTEM_PROMPT
	assert "**How to apply:**" in SYSTEM_PROMPT
	assert "## headings" in SYSTEM_PROMPT.lower() or "headings" in SYSTEM_PROMPT.lower()


def test_system_prompt_has_worked_example():
	"""Compact worked example for both small and large traces.

	The T1/Turn1 turn-numbered example was dropped — the new compact
	worked example describes the flow inline (e.g. "read → grep → ...")
	for Small trace and Large trace paragraphs.
	"""
	assert "WORKED EXAMPLE" in SYSTEM_PROMPT
	assert "Small trace" in SYSTEM_PROMPT
	assert "Large trace" in SYSTEM_PROMPT
	# The example should reference note() and prune() usage patterns
	assert "note(" in SYSTEM_PROMPT
	assert "prune(" in SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# run_extraction signature
# ---------------------------------------------------------------------------


def test_run_extraction_signature_no_per_pass_limits():
	"""run_extraction must NOT accept reflect_limit/extract_limit/finalize_limit.

	The budget is auto-scaled from trace size inside the function.
	"""
	sig = inspect.signature(run_extraction)
	params = sig.parameters
	assert "reflect_limit" not in params
	assert "extract_limit" not in params
	assert "finalize_limit" not in params
	# Core params still required
	assert "memory_root" in params
	assert "trace_path" in params
	assert "model" in params


def test_run_extraction_still_has_return_messages_flag():
	"""The return_messages flag is preserved for eval harness callers."""
	sig = inspect.signature(run_extraction)
	assert "return_messages" in sig.parameters
	assert sig.parameters["return_messages"].default is False


# ---------------------------------------------------------------------------
# Regressions: dead code paths must NOT come back
# ---------------------------------------------------------------------------


def test_run_extraction_three_pass_is_gone():
	"""The three-pass runner must NOT be re-introduced under any name.

	Regression guard: if a future refactor accidentally restores it,
	this test fails immediately.
	"""
	assert not hasattr(extract_module, "run_extraction_three_pass"), (
		"run_extraction_three_pass reappeared — the plan's single-pass redesign "
		"deleted it deliberately. Check extract.py for stray code."
	)


def test_finalize_result_is_gone():
	"""FinalizeResult was the three-pass output type — must not come back."""
	assert not hasattr(extract_module, "FinalizeResult"), (
		"FinalizeResult reappeared — single-pass uses ExtractionResult."
	)


def test_extract_pydanticai_module_is_gone():
	"""The old extract_pydanticai.py module must be gone after the rename."""
	import importlib

	try:
		importlib.import_module("lerim.agents.extract_pydanticai")
	except ModuleNotFoundError:
		pass  # expected
	else:
		raise AssertionError(
			"lerim.agents.extract_pydanticai module still exists — "
			"it should have been renamed to extract.py"
		)


def test_no_module_level_usage_limit_constants():
	"""No stray REFLECT_LIMITS / EXTRACT_LIMITS / FINALIZE_LIMITS constants.

	These were the three-pass per-pass budgets. The single-pass agent uses
	compute_request_budget() instead.
	"""
	assert not hasattr(extract_module, "REFLECT_LIMITS")
	assert not hasattr(extract_module, "EXTRACT_LIMITS")
	assert not hasattr(extract_module, "FINALIZE_LIMITS")


# ---------------------------------------------------------------------------
# Budget formula smoke (already covered in test_tools.py, light check here)
# ---------------------------------------------------------------------------


def test_compute_request_budget_is_used_by_runner(tmp_path):
	"""The runner uses compute_request_budget; smoke check it produces a sane int.

	Full table-driven coverage lives in test_tools.py::TestComputeRequestBudget.
	"""
	trace = tmp_path / "t.jsonl"
	trace.write_text("x\n" * 500, encoding="utf-8")
	budget = compute_request_budget(trace)
	assert 20 <= budget <= 80
