"""Unit tests for DSPy ReAct sync, maintain, and ask agents + LerimRuntime."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import dspy
import pytest

from lerim.config.settings import AgentRoleConfig
from lerim.runtime.context import RuntimeContext, build_context
from lerim.runtime.runtime import LerimRuntime, _trajectory_to_trace_list
from lerim.runtime.sync_agent import SyncAgent, SyncSignature
from lerim.runtime.maintain_agent import MaintainAgent, MaintainSignature
from lerim.runtime.ask_agent import AskAgent, AskSignature
from tests.helpers import make_config


# ---------------------------------------------------------------------------
# Test helper
# ---------------------------------------------------------------------------


def _make_ctx(tmp_path: Path, **overrides) -> RuntimeContext:
	"""Build a RuntimeContext for testing."""
	mem_root = tmp_path / "memories"
	mem_root.mkdir(exist_ok=True)
	(mem_root / "decisions").mkdir(exist_ok=True)
	(mem_root / "learnings").mkdir(exist_ok=True)
	run_folder = tmp_path / "runs" / "test-run"
	run_folder.mkdir(parents=True, exist_ok=True)

	defaults = dict(
		repo_root=tmp_path,
		memory_root=mem_root,
		workspace_root=tmp_path / "workspace",
		run_folder=run_folder,
		extra_read_roots=(),
		run_id="test-run-001",
		config=make_config(tmp_path),
	)
	defaults.update(overrides)
	return build_context(**defaults)


# ---------------------------------------------------------------------------
# Signature docstring tests
# ---------------------------------------------------------------------------


def test_sync_signature_contains_steps():
	"""SyncSignature docstring should contain all 4 steps."""
	assert "EXTRACT" in SyncSignature.__doc__
	assert "BATCH DEDUP" in SyncSignature.__doc__
	assert "CLASSIFY AND WRITE" in SyncSignature.__doc__
	assert "WRITE REPORT" in SyncSignature.__doc__


def test_sync_signature_contains_dedup_rules():
	"""SyncSignature docstring should describe dedup thresholds."""
	assert "top_similarity" in SyncSignature.__doc__
	assert "0.65" in SyncSignature.__doc__
	assert "no_op" in SyncSignature.__doc__


def test_maintain_signature_contains_steps():
	"""MaintainSignature docstring should contain all major steps."""
	assert "SCAN" in MaintainSignature.__doc__
	assert "CROSS-SESSION" in MaintainSignature.__doc__
	assert "ARCHIVE" in MaintainSignature.__doc__
	assert "HOT MEMORY" in MaintainSignature.__doc__


def test_maintain_signature_contains_cross_session():
	"""MaintainSignature docstring should reference cross-session analysis details."""
	doc = MaintainSignature.__doc__
	assert "Signal Amplification" in doc or "signal" in doc.lower()
	assert "Contradiction" in doc or "contradiction" in doc.lower()
	assert "Gap Detection" in doc or "gap" in doc.lower()
	assert "Cross-Agent Patterns" in doc


def test_maintain_signature_contains_decay():
	"""MaintainSignature docstring should include decay policy references."""
	doc = MaintainSignature.__doc__
	assert "DECAY" in doc or "decay" in doc


def test_ask_signature_contains_search():
	"""AskSignature docstring should reference memory_search."""
	assert "memory_search" in AskSignature.__doc__


def test_ask_signature_contains_layout():
	"""AskSignature docstring should describe memory layout."""
	doc = AskSignature.__doc__
	assert "decisions" in doc
	assert "learnings" in doc
	assert "summaries" in doc


# ---------------------------------------------------------------------------
# Signature field tests (typed fields, not task_context)
# ---------------------------------------------------------------------------


def test_sync_signature_has_typed_fields():
	"""SyncSignature should have individual typed InputFields, not task_context."""
	fields = SyncSignature.model_fields
	assert "trace_path" in fields
	assert "memory_root" in fields
	assert "run_folder" in fields
	assert "extract_artifact_path" in fields
	assert "memory_actions_path" in fields
	assert "run_id" in fields
	assert "task_context" not in fields


def test_maintain_signature_has_typed_fields():
	"""MaintainSignature should have individual typed InputFields, not task_context."""
	fields = MaintainSignature.model_fields
	assert "memory_root" in fields
	assert "run_folder" in fields
	assert "maintain_actions_path" in fields
	assert "hot_memory_path" in fields
	assert "access_stats" in fields
	assert "task_context" not in fields


def test_ask_signature_has_typed_fields():
	"""AskSignature should have individual typed InputFields, not task_context."""
	fields = AskSignature.model_fields
	assert "question" in fields
	assert "memory_root" in fields
	assert "hints" in fields
	assert "task_context" not in fields


# ---------------------------------------------------------------------------
# Module construction tests
# ---------------------------------------------------------------------------


def test_sync_agent_construction(tmp_path):
	"""SyncAgent should create a dspy.ReAct module with a react attribute."""
	ctx = _make_ctx(tmp_path)
	agent = SyncAgent(ctx)
	assert hasattr(agent, "react")
	# DSPy ReAct collapses functools.partial tools by type name;
	# verify the bind function returns the expected count instead.
	from lerim.runtime.tools import bind_sync_tools
	assert len(bind_sync_tools(ctx)) == 7


def test_maintain_agent_construction(tmp_path):
	"""MaintainAgent should create a dspy.ReAct module with a react attribute."""
	ctx = _make_ctx(tmp_path)
	agent = MaintainAgent(ctx)
	assert hasattr(agent, "react")
	from lerim.runtime.tools import bind_maintain_tools
	assert len(bind_maintain_tools(ctx)) == 8


def test_ask_agent_construction(tmp_path):
	"""AskAgent should create a dspy.ReAct module with a react attribute."""
	ctx = _make_ctx(tmp_path)
	agent = AskAgent(ctx)
	assert hasattr(agent, "react")
	from lerim.runtime.tools import bind_ask_tools
	assert len(bind_ask_tools(ctx)) == 3


def test_sync_agent_is_dspy_module(tmp_path):
	"""SyncAgent should be a dspy.Module subclass."""
	ctx = _make_ctx(tmp_path)
	agent = SyncAgent(ctx)
	assert isinstance(agent, dspy.Module)


def test_maintain_agent_is_dspy_module(tmp_path):
	"""MaintainAgent should be a dspy.Module subclass."""
	ctx = _make_ctx(tmp_path)
	agent = MaintainAgent(ctx)
	assert isinstance(agent, dspy.Module)


def test_ask_agent_is_dspy_module(tmp_path):
	"""AskAgent should be a dspy.Module subclass."""
	ctx = _make_ctx(tmp_path)
	agent = AskAgent(ctx)
	assert isinstance(agent, dspy.Module)


# ---------------------------------------------------------------------------
# Named predictors tests (optimization readiness)
# ---------------------------------------------------------------------------


def test_sync_agent_named_predictors(tmp_path):
	"""SyncAgent should expose named predictors for optimization."""
	ctx = _make_ctx(tmp_path)
	agent = SyncAgent(ctx)
	predictors = agent.named_predictors()
	# ReAct always has at least 2 predictors (react + extract)
	assert len(predictors) >= 2


def test_maintain_agent_named_predictors(tmp_path):
	"""MaintainAgent should expose named predictors for optimization."""
	ctx = _make_ctx(tmp_path)
	agent = MaintainAgent(ctx)
	predictors = agent.named_predictors()
	# ReAct always has at least 2 predictors (react + extract)
	assert len(predictors) >= 2


def test_ask_agent_named_predictors(tmp_path):
	"""AskAgent should expose named predictors for optimization."""
	ctx = _make_ctx(tmp_path)
	agent = AskAgent(ctx)
	predictors = agent.named_predictors()
	# ReAct always has at least 2 predictors (react + extract)
	assert len(predictors) >= 2


# ---------------------------------------------------------------------------
# Prompt helper tests (format_access_stats_section, format_ask_hints)
# ---------------------------------------------------------------------------


def test_format_access_stats_with_stats():
	"""format_access_stats_section should include stats and decay policy."""
	from lerim.runtime.maintain_agent import format_access_stats_section
	stats = [
		{
			"memory_id": "20260301-test",
			"last_accessed": "2026-03-01T10:00:00Z",
			"access_count": 5,
		},
	]
	result = format_access_stats_section(
		stats,
		decay_days=180,
		decay_archive_threshold=0.2,
		decay_min_confidence_floor=0.1,
		decay_recent_access_grace_days=30,
	)
	assert "20260301-test" in result
	assert "DECAY POLICY" in result


def test_format_access_stats_without_stats():
	"""format_access_stats_section without stats should skip decay."""
	from lerim.runtime.maintain_agent import format_access_stats_section
	result = format_access_stats_section(
		None,
		decay_days=180,
		decay_archive_threshold=0.2,
		decay_min_confidence_floor=0.1,
		decay_recent_access_grace_days=30,
	)
	assert "No access data available" in result


def test_maintain_artifact_paths(tmp_path):
	"""Maintain artifact paths should include standard keys."""
	from lerim.runtime.helpers import build_maintain_artifact_paths
	run_folder = tmp_path / "workspace" / "maintain-test"
	paths = build_maintain_artifact_paths(run_folder)
	assert "maintain_actions" in paths
	assert "agent_log" in paths
	assert "subagents_log" in paths


def test_format_ask_hints_with_hits():
	"""format_ask_hints should include pre-fetched hits."""
	from lerim.runtime.ask_agent import format_ask_hints
	hits = [
		{
			"id": "mem-1",
			"confidence": 0.9,
			"title": "Deploy tips",
			"_body": "Use CI.",
		},
	]
	result = format_ask_hints(hits, [])
	assert "mem-1" in result
	assert "Deploy tips" in result


def test_format_ask_hints_with_context_docs():
	"""format_ask_hints should include context docs."""
	from lerim.runtime.ask_agent import format_ask_hints
	docs = [
		{
			"doc_id": "doc-1",
			"title": "CI Setup",
			"body": "Configure pipelines.",
		},
	]
	result = format_ask_hints([], docs)
	assert "doc-1" in result
	assert "CI Setup" in result


def test_format_ask_hints_empty():
	"""format_ask_hints with no data should return placeholder text."""
	from lerim.runtime.ask_agent import format_ask_hints
	result = format_ask_hints([], [])
	assert "no relevant memories" in result


# ---------------------------------------------------------------------------
# Runtime construction tests
# ---------------------------------------------------------------------------


def _runtime_config(tmp_path, **overrides):
	"""Build a config with openrouter_api_key set for runtime tests."""
	cfg = make_config(tmp_path)
	defaults = dict(openrouter_api_key="test-key")
	defaults.update(overrides)
	return replace(cfg, **defaults)


def test_runtime_init(tmp_path):
	"""LerimRuntime should initialize with lead LM."""
	config = _runtime_config(tmp_path)
	runtime = LerimRuntime(config=config)
	assert hasattr(runtime, "_lead_lm")
	assert isinstance(runtime._lead_lm, dspy.LM)


def test_runtime_sync_missing_trace(tmp_path):
	"""sync() should raise FileNotFoundError for missing trace."""
	config = _runtime_config(tmp_path)
	runtime = LerimRuntime(config=config, default_cwd=str(tmp_path))
	with pytest.raises(FileNotFoundError, match="trace_path_missing"):
		runtime.sync(trace_path="/nonexistent/trace.jsonl")


def test_runtime_init_builds_fallback_lms(tmp_path):
	"""LerimRuntime should build fallback LMs from config."""
	cfg = make_config(tmp_path)
	role = AgentRoleConfig(
		provider="openrouter",
		model="x-ai/grok-4.1-fast",
		api_base="",
		fallback_models=("openrouter:qwen/qwen3-coder",),
		timeout_seconds=120,
		max_iterations=10,
		openrouter_provider_order=(),
	)
	cfg = replace(cfg, lead_role=role, openrouter_api_key="test-key")
	runtime = LerimRuntime(default_cwd=str(tmp_path), config=cfg)
	assert len(runtime._fallback_lms) == 1


def test_runtime_init_no_fallback_lms(tmp_path):
	"""LerimRuntime with no fallbacks should have empty list."""
	cfg = _runtime_config(tmp_path)
	runtime = LerimRuntime(default_cwd=str(tmp_path), config=cfg)
	assert runtime._fallback_lms == []


# ---------------------------------------------------------------------------
# Quota error detection tests
# ---------------------------------------------------------------------------


def test_is_quota_error_429():
	"""_is_quota_error should detect HTTP 429 status codes."""
	assert LerimRuntime._is_quota_error("Error code: 429")


def test_is_quota_error_rate_limit():
	"""_is_quota_error should detect 'rate limit' text (case-insensitive)."""
	assert LerimRuntime._is_quota_error("rate limit exceeded")


def test_is_quota_error_quota():
	"""_is_quota_error should detect 'quota' text (case-insensitive)."""
	assert LerimRuntime._is_quota_error("Quota exceeded for this billing period")


def test_is_quota_error_negative():
	"""_is_quota_error should return False for non-quota errors."""
	assert not LerimRuntime._is_quota_error("timeout error")
	assert not LerimRuntime._is_quota_error("Internal server error 500")
	assert not LerimRuntime._is_quota_error("Connection timeout")


# ---------------------------------------------------------------------------
# Trajectory adapter tests
# ---------------------------------------------------------------------------


def test_trajectory_to_trace_list():
	"""_trajectory_to_trace_list should convert ReAct trajectory to trace list."""
	trajectory = {
		"thought_0": "I should extract memories",
		"tool_name_0": "extract_pipeline",
		"tool_args_0": {"guidance": ""},
		"observation_0": '{"output_path": "/tmp/extract.json", "candidate_count": 3}',
		"thought_1": "Now summarize",
		"tool_name_1": "summarize_pipeline",
		"tool_args_1": {},
		"observation_1": '{"summary_path": "/tmp/summary.md"}',
	}
	trace = _trajectory_to_trace_list(trajectory)
	assert len(trace) == 6  # 2 iterations x 3 entries each
	assert trace[0]["role"] == "assistant"
	assert trace[0]["content"] == "I should extract memories"
	assert trace[1]["role"] == "assistant"
	assert trace[1]["tool_call"]["name"] == "extract_pipeline"
	assert trace[1]["tool_call"]["arguments"] == {"guidance": ""}
	assert trace[2]["role"] == "tool"
	assert trace[2]["name"] == "extract_pipeline"
	assert "extract.json" in trace[2]["content"]
	# Second iteration
	assert trace[3]["content"] == "Now summarize"
	assert trace[4]["tool_call"]["name"] == "summarize_pipeline"
	assert trace[5]["role"] == "tool"


def test_trajectory_to_trace_list_empty():
	"""Empty trajectory should produce empty trace list."""
	trace = _trajectory_to_trace_list({})
	assert trace == []


def test_trajectory_to_trace_list_single_step():
	"""Single-step trajectory should produce 3 entries."""
	trajectory = {
		"thought_0": "Done",
		"tool_name_0": "write_report",
		"tool_args_0": {"file_path": "/tmp/r.json", "content": "{}"},
		"observation_0": "Report written",
	}
	trace = _trajectory_to_trace_list(trajectory)
	assert len(trace) == 3


# ---------------------------------------------------------------------------
# Session ID tests
# ---------------------------------------------------------------------------


def test_generate_session_id():
	"""generate_session_id should produce a unique 'lerim-' prefixed ID."""
	sid = LerimRuntime.generate_session_id()
	assert sid.startswith("lerim-")
	assert len(sid) > 10


def test_generate_session_id_uniqueness():
	"""Two generated session IDs should be different."""
	sid1 = LerimRuntime.generate_session_id()
	sid2 = LerimRuntime.generate_session_id()
	assert sid1 != sid2


# ---------------------------------------------------------------------------
# Schema tests (from old test_oai_agent.py)
# ---------------------------------------------------------------------------


def test_memory_candidate_outcome_field():
	"""MemoryCandidate should support the outcome field."""
	from lerim.memory.schemas import MemoryCandidate
	c = MemoryCandidate(
		primitive="learning",
		kind="insight",
		title="Test",
		body="Test content",
		confidence=0.8,
		outcome="worked",
	)
	assert c.outcome == "worked"


def test_memory_candidate_outcome_default_none():
	"""MemoryCandidate outcome should default to None."""
	from lerim.memory.schemas import MemoryCandidate
	c = MemoryCandidate(
		primitive="decision",
		title="Test",
		body="Test content",
	)
	assert c.outcome is None


def test_memory_record_outcome_in_frontmatter():
	"""MemoryRecord with outcome should include it in frontmatter."""
	from lerim.memory.memory_record import MemoryRecord
	r = MemoryRecord(
		id="test",
		primitive="learning",
		kind="pitfall",
		title="Test",
		body="Content",
		confidence=0.8,
		outcome="failed",
		source="test-run",
	)
	fm = r.to_frontmatter_dict()
	assert fm["outcome"] == "failed"
	md = r.to_markdown()
	assert "outcome: failed" in md


def test_memory_record_no_outcome_in_frontmatter():
	"""MemoryRecord without outcome should not include it in frontmatter."""
	from lerim.memory.memory_record import MemoryRecord
	r = MemoryRecord(
		id="test",
		primitive="decision",
		title="Test",
		body="Content",
		confidence=0.9,
		source="test-run",
	)
	fm = r.to_frontmatter_dict()
	assert "outcome" not in fm


# ---------------------------------------------------------------------------
# Fallback retry logic tests (mocked, no LLM calls)
# ---------------------------------------------------------------------------


def test_run_with_fallback_succeeds_on_primary(tmp_path, monkeypatch):
	"""_run_with_fallback should return on first success without trying fallbacks."""
	cfg = make_config(tmp_path)
	role = AgentRoleConfig(
		provider="openrouter",
		model="x-ai/grok-4.1-fast",
		api_base="",
		fallback_models=("openrouter:qwen/qwen3-coder",),
		timeout_seconds=120,
		max_iterations=10,
		openrouter_provider_order=(),
	)
	cfg = replace(cfg, lead_role=role, openrouter_api_key="test-key")
	runtime = LerimRuntime(default_cwd=str(tmp_path), config=cfg)

	call_count = 0

	class FakeModule(dspy.Module):
		def forward(self, **kwargs):
			nonlocal call_count
			call_count += 1
			return dspy.Prediction(completion_summary="success")

	result = runtime._run_with_fallback(
		flow="test",
		module=FakeModule(),
		input_args={"question": "test prompt"},
	)
	assert result.completion_summary == "success"
	assert call_count == 1


def test_run_with_fallback_switches_on_quota_error(tmp_path, monkeypatch):
	"""_run_with_fallback should switch to fallback model on quota error."""
	import lerim.runtime.runtime as runtime_mod
	monkeypatch.setattr(runtime_mod.time, "sleep", lambda _: None)

	cfg = make_config(tmp_path)
	role = AgentRoleConfig(
		provider="openrouter",
		model="x-ai/grok-4.1-fast",
		api_base="",
		fallback_models=("openrouter:qwen/qwen3-coder",),
		timeout_seconds=120,
		max_iterations=10,
		openrouter_provider_order=(),
	)
	cfg = replace(cfg, lead_role=role, openrouter_api_key="test-key")
	runtime = LerimRuntime(default_cwd=str(tmp_path), config=cfg)

	models_tried = []

	class FakeModule(dspy.Module):
		def forward(self, **kwargs):
			if len(models_tried) == 0:
				models_tried.append("primary")
				raise RuntimeError("Error 429: Rate limit exceeded")
			models_tried.append("fallback")
			return dspy.Prediction(completion_summary="fallback success")

	result = runtime._run_with_fallback(
		flow="test",
		module=FakeModule(),
		input_args={"question": "test prompt"},
	)
	assert result.completion_summary == "fallback success"
	assert models_tried == ["primary", "fallback"]


def test_run_with_fallback_raises_when_all_exhausted(tmp_path, monkeypatch):
	"""_run_with_fallback should raise RuntimeError when all models fail."""
	import lerim.runtime.runtime as runtime_mod
	monkeypatch.setattr(runtime_mod.time, "sleep", lambda _: None)

	cfg = _runtime_config(tmp_path)
	runtime = LerimRuntime(default_cwd=str(tmp_path), config=cfg)

	class FakeModule(dspy.Module):
		def forward(self, **kwargs):
			raise RuntimeError("Connection timeout")

	with pytest.raises(RuntimeError, match="Failed after trying 1 model"):
		runtime._run_with_fallback(
			flow="test",
			module=FakeModule(),
			input_args={"question": "test prompt"},
		)


def test_run_with_fallback_retries_same_model_on_non_quota_error(
	tmp_path, monkeypatch,
):
	"""_run_with_fallback should retry same model on non-quota errors with backoff."""
	import lerim.runtime.runtime as runtime_mod
	monkeypatch.setattr(runtime_mod.time, "sleep", lambda _: None)

	cfg = _runtime_config(tmp_path)
	runtime = LerimRuntime(default_cwd=str(tmp_path), config=cfg)

	attempt_count = 0

	class FakeModule(dspy.Module):
		def forward(self, **kwargs):
			nonlocal attempt_count
			attempt_count += 1
			if attempt_count < 3:
				raise RuntimeError("Server error 500")
			return dspy.Prediction(completion_summary="recovered")

	result = runtime._run_with_fallback(
		flow="test",
		module=FakeModule(),
		input_args={"question": "test prompt"},
	)
	assert result.completion_summary == "recovered"
	assert attempt_count == 3


# ---------------------------------------------------------------------------
# is_within tests
# ---------------------------------------------------------------------------


def test_is_within_same_path(tmp_path):
	"""is_within should return True for the same path."""
	from lerim.runtime.helpers import is_within
	assert is_within(tmp_path, tmp_path) is True


def test_is_within_child_path(tmp_path):
	"""is_within should return True for a child path."""
	from lerim.runtime.helpers import is_within
	child = tmp_path / "sub" / "file.txt"
	assert is_within(child, tmp_path) is True


def test_is_within_outside_path(tmp_path):
	"""is_within should return False for an unrelated path."""
	from lerim.runtime.helpers import is_within
	assert is_within(Path("/tmp/other"), tmp_path) is False
