"""Unit tests for maintain/ask DSPy agents + LerimRuntime (sync = PydanticAI)."""

from __future__ import annotations

from dataclasses import replace

import dspy
import httpx
import pytest
from openai import RateLimitError

from lerim.config.settings import RoleConfig
from lerim.server.runtime import (
	LerimRuntime,
	_is_quota_error_pydantic,
	_trajectory_to_trace_list,
)
from lerim.agents.maintain import MaintainAgent, MaintainSignature
from lerim.agents.ask import AskAgent, AskSignature
from tests.helpers import make_config


# ---------------------------------------------------------------------------
# Signature docstring tests
# ---------------------------------------------------------------------------


def test_maintain_signature_contains_steps():
	"""MaintainSignature docstring should contain all major phases."""
	doc = MaintainSignature.__doc__
	assert 'name="orient"' in doc
	assert 'name="gather_signal"' in doc
	assert 'name="consolidate"' in doc
	assert 'name="prune_and_index"' in doc


def test_maintain_signature_contains_consolidation():
	"""MaintainSignature docstring should reference memory consolidation."""
	doc = MaintainSignature.__doc__
	assert "Merge" in doc or "merge" in doc.lower()
	assert "archive" in doc.lower()
	assert "contradict" in doc.lower()


def test_maintain_signature_contains_tool_names():
	"""MaintainSignature docstring should reference new tool names."""
	doc = MaintainSignature.__doc__
	assert "scan()" in doc
	assert "read(" in doc
	assert "write(" in doc
	assert "edit(" in doc
	assert "archive(" in doc


def test_ask_signature_contains_tool_names():
	"""AskSignature docstring should reference scan()."""
	doc = AskSignature.__doc__
	assert "scan()" in doc
	assert "read(" in doc


def test_ask_signature_contains_layout():
	"""AskSignature docstring should describe memory layout."""
	doc = AskSignature.__doc__
	assert "feedback" in doc
	assert "project" in doc
	assert "user" in doc
	assert "reference" in doc
	assert "summaries" in doc


# ---------------------------------------------------------------------------
# Signature field tests (typed fields, not task_context)
# ---------------------------------------------------------------------------


def test_maintain_signature_output_field():
	"""MaintainSignature should have completion_summary output."""
	fields = MaintainSignature.model_fields
	assert "completion_summary" in fields


def test_ask_signature_has_typed_fields():
	"""AskSignature should have individual typed InputFields."""
	fields = AskSignature.model_fields
	assert "question" in fields
	assert "hints" in fields
	assert "answer" in fields
	assert "task_context" not in fields


# ---------------------------------------------------------------------------
# Module construction tests
# ---------------------------------------------------------------------------


def test_maintain_agent_construction(tmp_path):
	"""MaintainAgent should create a dspy.ReAct module with a react attribute."""
	mem_root = tmp_path / "memory"
	mem_root.mkdir()

	agent = MaintainAgent(memory_root=mem_root, max_iters=5)
	assert hasattr(agent, "react")


def test_ask_agent_construction(tmp_path):
	"""AskAgent should create a dspy.ReAct module with a react attribute."""
	mem_root = tmp_path / "memory"
	mem_root.mkdir()

	agent = AskAgent(memory_root=mem_root, max_iters=5)
	assert hasattr(agent, "react")


def test_maintain_agent_is_dspy_module(tmp_path):
	"""MaintainAgent should be a dspy.Module subclass."""
	mem_root = tmp_path / "memory"
	mem_root.mkdir()

	agent = MaintainAgent(memory_root=mem_root, max_iters=5)
	assert isinstance(agent, dspy.Module)


def test_ask_agent_is_dspy_module(tmp_path):
	"""AskAgent should be a dspy.Module subclass."""
	mem_root = tmp_path / "memory"
	mem_root.mkdir()

	agent = AskAgent(memory_root=mem_root, max_iters=5)
	assert isinstance(agent, dspy.Module)


# ---------------------------------------------------------------------------
# Named predictors tests (optimization readiness)
# ---------------------------------------------------------------------------


def test_maintain_agent_named_predictors(tmp_path):
	"""MaintainAgent should expose named predictors for optimization."""
	mem_root = tmp_path / "memory"
	mem_root.mkdir()

	agent = MaintainAgent(memory_root=mem_root, max_iters=5)
	predictors = agent.named_predictors()
	assert len(predictors) >= 2


def test_ask_agent_named_predictors(tmp_path):
	"""AskAgent should expose named predictors for optimization."""
	mem_root = tmp_path / "memory"
	mem_root.mkdir()

	agent = AskAgent(memory_root=mem_root, max_iters=5)
	predictors = agent.named_predictors()
	assert len(predictors) >= 2


# ---------------------------------------------------------------------------
# Prompt helper tests (format_ask_hints)
# ---------------------------------------------------------------------------


def test_maintain_artifact_paths(tmp_path):
	"""Maintain artifact paths should include standard keys."""
	from lerim.server.runtime import build_maintain_artifact_paths
	run_folder = tmp_path / "workspace" / "maintain-test"
	paths = build_maintain_artifact_paths(run_folder)
	assert "agent_log" in paths
	assert "subagents_log" in paths
	assert "maintain_actions" not in paths


def test_format_ask_hints_with_hits():
	"""format_ask_hints should include pre-fetched hits."""
	from lerim.agents.ask import format_ask_hints
	hits = [
		{
			"type": "feedback",
			"name": "Deploy tips",
			"description": "CI pipeline best practices",
			"body": "Use CI.",
		},
	]
	result = format_ask_hints(hits, [])
	assert "Deploy tips" in result
	assert "feedback" in result


def test_format_ask_hints_with_context_docs():
	"""format_ask_hints should include context docs."""
	from lerim.agents.ask import format_ask_hints
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
	from lerim.agents.ask import format_ask_hints
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
	"""LerimRuntime should initialize with agent LM."""
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
	role = RoleConfig(
		provider="openrouter",
		model="x-ai/grok-4.1-fast",
		fallback_models=("openrouter:qwen/qwen3-coder",),
	)
	cfg = replace(cfg, agent_role=role, openrouter_api_key="test-key")
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
		"thought_0": "I should read the trace",
		"tool_name_0": "read",
		"tool_args_0": {"target": "trace", "limit": 200},
		"observation_0": '{"messages": []}',
		"thought_1": "Now scan existing memories",
		"tool_name_1": "scan",
		"tool_args_1": {},
		"observation_1": '{"count": 0, "memories": []}',
	}
	trace = _trajectory_to_trace_list(trajectory)
	assert len(trace) == 6  # 2 iterations x 3 entries each
	assert trace[0]["role"] == "assistant"
	assert trace[0]["content"] == "I should read the trace"
	assert trace[1]["role"] == "assistant"
	assert trace[1]["tool_call"]["name"] == "read"
	assert trace[1]["tool_call"]["arguments"] == {"target": "trace", "limit": 200}
	assert trace[2]["role"] == "tool"
	assert trace[2]["name"] == "read"
	assert "messages" in trace[2]["content"]
	# Second iteration
	assert trace[3]["content"] == "Now scan existing memories"
	assert trace[4]["tool_call"]["name"] == "scan"
	assert trace[5]["role"] == "tool"


def test_trajectory_to_trace_list_empty():
	"""Empty trajectory should produce empty trace list."""
	trace = _trajectory_to_trace_list({})
	assert trace == []


def test_trajectory_to_trace_list_single_step():
	"""Single-step trajectory should produce 3 entries."""
	trajectory = {
		"thought_0": "Done",
		"tool_name_0": "write",
		"tool_args_0": {
			"type": "project",
			"name": "Test",
			"description": "Desc",
			"body": "Body **Why:** x **How to apply:** y",
		},
		"observation_0": '{"file_path": "/tmp/m.md"}',
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
# Quota detection (PydanticAI) — isinstance + string-fallback
# ---------------------------------------------------------------------------


def _make_rate_limit_error() -> RateLimitError:
	"""Build a real openai.RateLimitError for isinstance-based detection."""
	return RateLimitError(
		message="rate limited",
		response=httpx.Response(
			429, request=httpx.Request("POST", "https://test"),
		),
		body=None,
	)


def test_is_quota_error_pydantic_rate_limit_instance():
	"""openai.RateLimitError instance should be detected as a quota error."""
	assert _is_quota_error_pydantic(_make_rate_limit_error())


def test_is_quota_error_pydantic_httpx_429():
	"""httpx.HTTPStatusError with status 429 should be detected as a quota error."""
	request = httpx.Request("POST", "https://test")
	response = httpx.Response(429, request=request)
	exc = httpx.HTTPStatusError("rate limit", request=request, response=response)
	assert _is_quota_error_pydantic(exc)


def test_is_quota_error_pydantic_string_fallback():
	"""String fallback catches wrapped providers that say 'rate limit'."""
	assert _is_quota_error_pydantic(RuntimeError("http 429 rate limit"))
	assert _is_quota_error_pydantic(RuntimeError("quota exceeded"))
	assert _is_quota_error_pydantic(RuntimeError("Rate Limit hit"))


def test_is_quota_error_pydantic_non_quota_returns_false():
	"""Non-quota errors must not be treated as quota errors."""
	assert not _is_quota_error_pydantic(RuntimeError("connection refused"))
	assert not _is_quota_error_pydantic(ValueError("bad input"))


# ---------------------------------------------------------------------------
# Fallback retry logic tests (PydanticAI callable-based, no LLM calls)
# ---------------------------------------------------------------------------


def _build_sync_runtime(tmp_path, monkeypatch, *, fallback_count: int = 0):
	"""Build a LerimRuntime with mocked DSPy LM builders for runtime construction."""
	from unittest.mock import MagicMock

	cfg = make_config(tmp_path)
	role = RoleConfig(
		provider="openrouter",
		model="x-ai/grok-4.1-fast",
		fallback_models=tuple(
			f"openrouter:fallback-{i}" for i in range(fallback_count)
		),
	)
	cfg = replace(cfg, agent_role=role, openrouter_api_key="test-key")

	monkeypatch.setattr(
		"lerim.server.runtime.build_dspy_lm", lambda *a, **kw: MagicMock()
	)
	monkeypatch.setattr(
		"lerim.server.runtime.build_dspy_fallback_lms", lambda *a, **kw: []
	)
	monkeypatch.setattr(
		"lerim.config.providers.validate_provider_for_role",
		lambda *a, **kw: None,
	)
	return LerimRuntime(default_cwd=str(tmp_path), config=cfg)


def test_run_with_fallback_succeeds_on_primary(tmp_path, monkeypatch):
	"""_run_with_fallback returns on first success without trying fallbacks."""
	runtime = _build_sync_runtime(tmp_path, monkeypatch, fallback_count=1)

	call_count = 0

	def fake_call(model):
		"""Succeed on the first call so no fallback is needed."""
		nonlocal call_count
		call_count += 1
		return "primary-success"

	result = runtime._run_with_fallback(
		flow="test",
		callable_fn=fake_call,
		model_builders=[lambda: "primary", lambda: "fallback"],
	)
	assert result == "primary-success"
	assert call_count == 1


def test_run_with_fallback_switches_on_quota_error(tmp_path, monkeypatch):
	"""Quota errors should trigger switch to the next fallback model builder."""
	import lerim.server.runtime as runtime_mod
	monkeypatch.setattr(runtime_mod.time, "sleep", lambda _: None)
	runtime = _build_sync_runtime(tmp_path, monkeypatch, fallback_count=1)

	seen_models = []

	def fake_call(model):
		"""Fail on primary with RateLimitError, succeed on fallback."""
		seen_models.append(model)
		if model == "primary":
			raise _make_rate_limit_error()
		return "fallback-success"

	result = runtime._run_with_fallback(
		flow="test",
		callable_fn=fake_call,
		model_builders=[lambda: "primary", lambda: "fallback"],
	)
	assert result == "fallback-success"
	assert seen_models == ["primary", "fallback"]


def test_run_with_fallback_raises_when_all_exhausted(tmp_path, monkeypatch):
	"""_run_with_fallback raises RuntimeError when all models + attempts fail."""
	import lerim.server.runtime as runtime_mod
	monkeypatch.setattr(runtime_mod.time, "sleep", lambda _: None)
	runtime = _build_sync_runtime(tmp_path, monkeypatch, fallback_count=0)

	def always_fail(model):
		"""Non-quota transient error that should retry then exhaust."""
		raise RuntimeError("connection refused")

	with pytest.raises(RuntimeError, match="Failed after trying 1 model"):
		runtime._run_with_fallback(
			flow="test",
			callable_fn=always_fail,
			model_builders=[lambda: "primary"],
			max_attempts=2,
		)


def test_run_with_fallback_retries_same_model_on_transient(tmp_path, monkeypatch):
	"""Non-quota errors retry the same model with backoff until success."""
	import lerim.server.runtime as runtime_mod
	monkeypatch.setattr(runtime_mod.time, "sleep", lambda _: None)
	runtime = _build_sync_runtime(tmp_path, monkeypatch, fallback_count=0)

	attempts = 0

	def sometimes_fail(model):
		"""Fail twice with a non-quota error, then succeed on the third try."""
		nonlocal attempts
		attempts += 1
		if attempts < 3:
			raise RuntimeError("Internal server error 500")
		return "recovered"

	result = runtime._run_with_fallback(
		flow="test",
		callable_fn=sometimes_fail,
		model_builders=[lambda: "primary"],
		max_attempts=3,
	)
	assert result == "recovered"
	assert attempts == 3


def test_run_with_fallback_usage_limit_short_circuits(tmp_path, monkeypatch):
	"""UsageLimitExceeded is re-raised immediately without retries or fallback."""
	from pydantic_ai.exceptions import UsageLimitExceeded

	import lerim.server.runtime as runtime_mod
	monkeypatch.setattr(runtime_mod.time, "sleep", lambda _: None)
	runtime = _build_sync_runtime(tmp_path, monkeypatch, fallback_count=1)

	call_count = 0

	def blow_budget(model):
		"""Exceed the local usage budget immediately."""
		nonlocal call_count
		call_count += 1
		raise UsageLimitExceeded("request_limit exceeded")

	with pytest.raises(UsageLimitExceeded):
		runtime._run_with_fallback(
			flow="test",
			callable_fn=blow_budget,
			model_builders=[lambda: "primary", lambda: "fallback"],
			max_attempts=3,
		)
	assert call_count == 1  # no retries, no fallback attempt


# ---------------------------------------------------------------------------
# Runtime imports (Phase 2 invariant)
# ---------------------------------------------------------------------------


def test_runtime_sync_imports_run_extraction():
	"""runtime.py must expose run_extraction (the single-pass sync callable)."""
	from lerim.server.runtime import run_extraction

	assert callable(run_extraction)


def test_runtime_sync_imports_pydantic_build_model():
	"""runtime.py must expose build_pydantic_model for the model builder path."""
	from lerim.server.runtime import build_pydantic_model

	assert callable(build_pydantic_model)
