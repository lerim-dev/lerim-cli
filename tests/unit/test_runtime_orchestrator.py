"""Unit tests for LerimRuntime DSPy ReAct orchestrator.

Tests sync, maintain, and ask flows with all LLM calls mocked.
Covers retry/fallback logic, trajectory conversion, cost tracking,
and artifact writing.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock

import dspy
import pytest

from tests.helpers import make_config


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_prediction(**fields) -> MagicMock:
	"""Build a mock dspy.Prediction with the given attributes."""
	pred = MagicMock(spec=dspy.Prediction)
	for key, val in fields.items():
		setattr(pred, key, val)
	# Ensure missing attrs return sensible defaults
	if "completion_summary" not in fields:
		pred.completion_summary = "test done"
	if "trajectory" not in fields:
		pred.trajectory = {}
	if "answer" not in fields:
		pred.answer = "test answer"
	return pred


def _build_runtime(tmp_path, monkeypatch, *, config=None):
	"""Construct a LerimRuntime with all provider calls mocked."""
	cfg = config or make_config(tmp_path)
	mock_lm = MagicMock()
	mock_lm.history = []

	monkeypatch.setattr(
		"lerim.server.runtime.build_dspy_lm", lambda *a, **kw: mock_lm
	)
	monkeypatch.setattr(
		"lerim.server.runtime.build_dspy_fallback_lms", lambda *a, **kw: []
	)
	monkeypatch.setattr(
		"lerim.config.providers.validate_provider_for_role", lambda *a, **kw: None
	)

	from lerim.server.runtime import LerimRuntime

	rt = LerimRuntime(default_cwd=str(tmp_path), config=cfg)
	return rt, mock_lm


# ---------------------------------------------------------------------------
# Trajectory conversion
# ---------------------------------------------------------------------------


class TestTrajectoryToTraceList:
	"""Tests for _trajectory_to_trace_list."""

	def test_empty_trajectory(self):
		"""Empty dict produces empty trace list."""
		from lerim.server.runtime import _trajectory_to_trace_list

		assert _trajectory_to_trace_list({}) == []

	def test_single_step(self):
		"""Single thought/tool/observation triple converts correctly."""
		from lerim.server.runtime import _trajectory_to_trace_list

		traj = {
			"thought_0": "I should read the trace.",
			"tool_name_0": "read",
			"tool_args_0": {"target": "trace"},
			"observation_0": "file contents here",
		}
		result = _trajectory_to_trace_list(traj)
		assert len(result) == 3
		assert result[0] == {"role": "assistant", "content": "I should read the trace."}
		assert result[1]["role"] == "assistant"
		assert result[1]["tool_call"]["name"] == "read"
		assert result[1]["tool_call"]["arguments"] == {"target": "trace"}
		assert result[2]["role"] == "tool"
		assert result[2]["content"] == "file contents here"

	def test_multiple_steps(self):
		"""Multiple numbered steps are all converted in order."""
		from lerim.server.runtime import _trajectory_to_trace_list

		traj = {
			"thought_0": "Step 1 thought",
			"tool_name_0": "tool_a",
			"tool_args_0": {},
			"observation_0": "result a",
			"thought_1": "Step 2 thought",
			"tool_name_1": "tool_b",
			"tool_args_1": {"x": 1},
			"observation_1": "result b",
		}
		result = _trajectory_to_trace_list(traj)
		assert len(result) == 6
		assert result[3]["content"] == "Step 2 thought"
		assert result[4]["tool_call"]["name"] == "tool_b"

	def test_missing_optional_fields(self):
		"""Missing tool_args and observation default gracefully."""
		from lerim.server.runtime import _trajectory_to_trace_list

		traj = {"thought_0": "thinking"}
		result = _trajectory_to_trace_list(traj)
		assert len(result) == 3
		assert result[1]["tool_call"]["arguments"] == {}
		assert result[2]["content"] == ""


# ---------------------------------------------------------------------------
# Cost tracking
# ---------------------------------------------------------------------------


class TestCostTracking:
	"""Tests for cost accumulation helpers."""

	def test_start_stop_cost_tracking(self):
		"""Start/stop returns zero when no cost added."""
		from lerim.server.runtime import start_cost_tracking, stop_cost_tracking

		start_cost_tracking()
		assert stop_cost_tracking() == 0.0

	def test_add_cost_accumulates(self):
		"""Costs accumulate between start and stop."""
		from lerim.server.runtime import (
			add_cost,
			start_cost_tracking,
			stop_cost_tracking,
		)

		start_cost_tracking()
		add_cost(0.01)
		add_cost(0.02)
		total = stop_cost_tracking()
		assert abs(total - 0.03) < 1e-9

	def test_add_cost_noop_when_not_tracking(self):
		"""add_cost is a no-op when tracking is not started."""
		from lerim.server.runtime import add_cost, stop_cost_tracking

		# Ensure tracking is off
		stop_cost_tracking()
		add_cost(1.0)  # should not raise

	def test_capture_dspy_cost_from_history(self):
		"""capture_dspy_cost extracts cost from LM history entries."""
		from lerim.server.runtime import (
			capture_dspy_cost,
			start_cost_tracking,
			stop_cost_tracking,
		)

		mock_usage = MagicMock()
		mock_usage.cost = 0.05
		mock_response = MagicMock()
		mock_response.usage = mock_usage

		lm = MagicMock()
		lm.history = [
			{"response": mock_response},
		]

		start_cost_tracking()
		capture_dspy_cost(lm, 0)
		total = stop_cost_tracking()
		assert abs(total - 0.05) < 1e-9

	def test_capture_dspy_cost_no_history(self):
		"""capture_dspy_cost handles LM with no history attribute."""
		from lerim.server.runtime import (
			capture_dspy_cost,
			start_cost_tracking,
			stop_cost_tracking,
		)

		lm = MagicMock(spec=[])  # no attributes
		start_cost_tracking()
		capture_dspy_cost(lm, 0)
		total = stop_cost_tracking()
		assert total == 0.0

	def test_capture_dspy_cost_dict_usage(self):
		"""capture_dspy_cost handles dict-style usage with cost key."""
		from lerim.server.runtime import (
			capture_dspy_cost,
			start_cost_tracking,
			stop_cost_tracking,
		)

		mock_response = MagicMock()
		mock_response.usage = {"cost": 0.10}

		lm = MagicMock()
		lm.history = [{"response": mock_response}]

		start_cost_tracking()
		capture_dspy_cost(lm, 0)
		total = stop_cost_tracking()
		assert abs(total - 0.10) < 1e-9


# ---------------------------------------------------------------------------
# Artifact I/O helpers
# ---------------------------------------------------------------------------


class TestArtifactIO:
	"""Tests for JSON artifact read/write helpers."""

	def test_write_json_artifact(self, tmp_path):
		"""_write_json_artifact writes valid JSON with trailing newline."""
		from lerim.server.runtime import _write_json_artifact

		p = tmp_path / "test.json"
		_write_json_artifact(p, {"key": "value"})
		text = p.read_text(encoding="utf-8")
		assert text.endswith("\n")
		assert json.loads(text) == {"key": "value"}

	def test_write_text_with_newline(self, tmp_path):
		"""_write_text_with_newline ensures trailing newline."""
		from lerim.server.runtime import _write_text_with_newline

		p = tmp_path / "t.txt"
		_write_text_with_newline(p, "hello")
		assert p.read_text(encoding="utf-8") == "hello\n"

		_write_text_with_newline(p, "world\n")
		assert p.read_text(encoding="utf-8") == "world\n"


# ---------------------------------------------------------------------------
# Static / class methods
# ---------------------------------------------------------------------------


class TestLerimRuntimeHelpers:
	"""Tests for static/class-level helpers on LerimRuntime."""

	def test_is_quota_error_429(self):
		"""429 in error message triggers quota detection."""
		from lerim.server.runtime import LerimRuntime

		assert LerimRuntime._is_quota_error("HTTP 429 Too Many Requests")

	def test_is_quota_error_rate_limit(self):
		"""Rate limit text triggers quota detection."""
		from lerim.server.runtime import LerimRuntime

		assert LerimRuntime._is_quota_error("Rate limit exceeded for model")

	def test_is_quota_error_quota(self):
		"""Quota text triggers quota detection."""
		from lerim.server.runtime import LerimRuntime

		assert LerimRuntime._is_quota_error("You have exceeded your quota")

	def test_is_not_quota_error(self):
		"""Normal errors are not quota errors."""
		from lerim.server.runtime import LerimRuntime

		assert not LerimRuntime._is_quota_error("Connection refused")

	def test_generate_session_id(self):
		"""Generated session IDs have the expected format."""
		from lerim.server.runtime import LerimRuntime

		sid = LerimRuntime.generate_session_id()
		assert sid.startswith("lerim-")
		assert len(sid) > 10


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


class TestPathHelpers:
	"""Tests for module-level path resolution helpers."""

	def test_default_run_folder_name(self):
		"""Run folder name has prefix and hex suffix."""
		from lerim.server.runtime import _default_run_folder_name

		name = _default_run_folder_name("sync")
		assert name.startswith("sync-")
		parts = name.split("-")
		assert len(parts) >= 3

	def test_build_artifact_paths(self, tmp_path):
		"""_build_artifact_paths returns expected keys."""
		from lerim.server.runtime import _build_artifact_paths

		paths = _build_artifact_paths(tmp_path / "run")
		assert "agent_log" in paths
		assert "subagents_log" in paths
		assert "session_log" in paths
		assert "summary" not in paths
		assert "memory_actions" not in paths

	def test_build_maintain_artifact_paths(self, tmp_path):
		"""build_maintain_artifact_paths returns expected keys."""
		from lerim.server.runtime import build_maintain_artifact_paths

		paths = build_maintain_artifact_paths(tmp_path / "run")
		assert "agent_log" in paths
		assert "subagents_log" in paths
		assert "maintain_actions" not in paths

	def test_resolve_runtime_roots_defaults(self, tmp_path):
		"""_resolve_runtime_roots uses config defaults when overrides are None."""
		from lerim.server.runtime import _resolve_runtime_roots

		cfg = make_config(tmp_path)
		mem, ws = _resolve_runtime_roots(
			config=cfg, memory_root=None, workspace_root=None
		)
		assert mem == cfg.memory_dir
		assert ws == cfg.data_dir / "workspace"

	def test_resolve_runtime_roots_overrides(self, tmp_path):
		"""_resolve_runtime_roots uses explicit overrides when provided."""
		from lerim.server.runtime import _resolve_runtime_roots

		cfg = make_config(tmp_path)
		custom_mem = tmp_path / "custom_mem"
		custom_ws = tmp_path / "custom_ws"
		mem, ws = _resolve_runtime_roots(
			config=cfg,
			memory_root=str(custom_mem),
			workspace_root=str(custom_ws),
		)
		assert mem == custom_mem.resolve()
		assert ws == custom_ws.resolve()


# ---------------------------------------------------------------------------
# _run_with_fallback
# ---------------------------------------------------------------------------


class TestRunWithFallback:
	"""Tests for retry and fallback model logic."""

	def test_success_first_attempt(self, tmp_path, monkeypatch):
		"""Module succeeds on first attempt -- no retries."""
		rt, _ = _build_runtime(tmp_path, monkeypatch)
		pred = _make_prediction()

		mock_module = MagicMock(return_value=pred)
		result = rt._run_with_fallback(
			flow="test", module=mock_module, input_args={"x": 1}
		)
		assert result is pred
		assert mock_module.call_count == 1

	def test_retry_on_transient_error(self, tmp_path, monkeypatch):
		"""Non-quota error retries up to max_attempts."""
		monkeypatch.setattr(time, "sleep", lambda _: None)
		rt, _ = _build_runtime(tmp_path, monkeypatch)
		pred = _make_prediction()

		call_count = 0

		def side_effect(**kwargs):
			"""Fail twice, succeed on third."""
			nonlocal call_count
			call_count += 1
			if call_count < 3:
				raise RuntimeError("500 Internal Server Error")
			return pred

		mock_module = MagicMock(side_effect=side_effect)
		result = rt._run_with_fallback(
			flow="test", module=mock_module, input_args={}, max_attempts=3
		)
		assert result is pred
		assert call_count == 3

	def test_quota_error_switches_to_fallback(self, tmp_path, monkeypatch):
		"""Quota error on primary model switches to fallback model."""
		from dataclasses import replace

		monkeypatch.setattr(time, "sleep", lambda _: None)

		cfg = make_config(tmp_path)
		cfg = replace(cfg, lead_role=replace(
			cfg.lead_role, fallback_models=("fallback/model-1",)
		))

		rt, _ = _build_runtime(tmp_path, monkeypatch, config=cfg)
		pred = _make_prediction()

		fallback_lm = MagicMock()
		rt._fallback_lms = [fallback_lm]

		call_count = 0

		def side_effect(**kwargs):
			"""Primary always fails with quota, fallback succeeds."""
			nonlocal call_count
			call_count += 1
			if call_count == 1:
				raise RuntimeError("HTTP 429 Too Many Requests")
			return pred

		mock_module = MagicMock(side_effect=side_effect)
		result = rt._run_with_fallback(
			flow="test", module=mock_module, input_args={}, max_attempts=2
		)
		assert result is pred

	def test_all_attempts_exhausted(self, tmp_path, monkeypatch):
		"""All models and attempts exhausted raises RuntimeError."""
		monkeypatch.setattr(time, "sleep", lambda _: None)
		rt, _ = _build_runtime(tmp_path, monkeypatch)

		mock_module = MagicMock(
			side_effect=RuntimeError("permanent failure")
		)
		with pytest.raises(RuntimeError, match="Failed after trying"):
			rt._run_with_fallback(
				flow="test", module=mock_module, input_args={}, max_attempts=2
			)


# ---------------------------------------------------------------------------
# Sync flow
# ---------------------------------------------------------------------------


class TestSyncFlow:
	"""Tests for sync() and _sync_inner()."""

	def test_sync_missing_trace_file(self, tmp_path, monkeypatch):
		"""sync() raises FileNotFoundError for non-existent trace file."""
		rt, _ = _build_runtime(tmp_path, monkeypatch)
		with pytest.raises(FileNotFoundError, match="trace_path_missing"):
			rt.sync(trace_path=tmp_path / "nonexistent.jsonl")

	def test_sync_happy_path(self, tmp_path, monkeypatch):
		"""sync() returns validated SyncResultContract payload on success."""
		rt, mock_lm = _build_runtime(tmp_path, monkeypatch)
		(tmp_path / "memory").mkdir(exist_ok=True)

		# Create trace file
		trace_file = tmp_path / "trace.jsonl"
		trace_file.write_text('{"type": "test"}\n', encoding="utf-8")

		# Mock agent modules
		pred = _make_prediction(
			completion_summary="Extracted 2 memories.",
			trajectory={
				"thought_0": "Analyzing trace",
				"tool_name_0": "write",
				"tool_args_0": {},
				"observation_0": "done",
			},
		)

		monkeypatch.setattr(
			"lerim.server.runtime.ExtractAgent",
			lambda **kw: MagicMock(return_value=pred),
		)

		# Patch logfire.span to be a no-op context manager
		mock_span = MagicMock()
		mock_span.__enter__ = MagicMock(return_value=mock_span)
		mock_span.__exit__ = MagicMock(return_value=False)
		monkeypatch.setattr("lerim.server.runtime.logfire.span", lambda *a, **kw: mock_span)

		result = rt.sync(
			trace_path=trace_file,
			memory_root=str(tmp_path / "memory"),
			workspace_root=str(tmp_path / "workspace"),
		)

		assert "trace_path" in result
		assert "cost_usd" in result
		assert isinstance(result["run_folder"], str)

	def test_sync_writes_artifacts(self, tmp_path, monkeypatch):
		"""sync() writes agent_log and agent_trace.json to run folder."""
		rt, _ = _build_runtime(tmp_path, monkeypatch)
		(tmp_path / "memory").mkdir(exist_ok=True)

		trace_file = tmp_path / "trace.jsonl"
		trace_file.write_text('{"type": "test"}\n', encoding="utf-8")

		pred = _make_prediction(
			completion_summary="Done.",
			trajectory={
				"thought_0": "Read file",
				"tool_name_0": "read",
				"tool_args_0": {"target": "trace"},
				"observation_0": "contents",
			},
		)
		monkeypatch.setattr(
			"lerim.server.runtime.ExtractAgent",
			lambda **kw: MagicMock(return_value=pred),
		)
		mock_span = MagicMock()
		mock_span.__enter__ = MagicMock(return_value=mock_span)
		mock_span.__exit__ = MagicMock(return_value=False)
		monkeypatch.setattr("lerim.server.runtime.logfire.span", lambda *a, **kw: mock_span)

		result = rt.sync(
			trace_path=trace_file,
			memory_root=str(tmp_path / "memory"),
			workspace_root=str(tmp_path / "workspace"),
		)

		run_folder = Path(result["run_folder"])
		assert (run_folder / "agent.log").exists()
		assert (run_folder / "agent_trace.json").exists()

		# Verify trace content
		trace_data = json.loads(
			(run_folder / "agent_trace.json").read_text(encoding="utf-8")
		)
		assert len(trace_data) == 3
		assert trace_data[0]["content"] == "Read file"


# ---------------------------------------------------------------------------
# Maintain flow
# ---------------------------------------------------------------------------


class TestMaintainFlow:
	"""Tests for maintain() and _maintain_inner()."""

	def test_maintain_happy_path(self, tmp_path, monkeypatch):
		"""maintain() returns validated MaintainResultContract on success."""
		rt, _ = _build_runtime(tmp_path, monkeypatch)
		(tmp_path / "memory").mkdir(exist_ok=True)

		pred = _make_prediction(
			completion_summary="Maintenance complete: merged 1, archived 2.",
			trajectory={},
		)
		monkeypatch.setattr(
			"lerim.server.runtime.MaintainAgent",
			lambda **kw: MagicMock(return_value=pred),
		)
		mock_span = MagicMock()
		mock_span.__enter__ = MagicMock(return_value=mock_span)
		mock_span.__exit__ = MagicMock(return_value=False)
		monkeypatch.setattr("lerim.server.runtime.logfire.span", lambda *a, **kw: mock_span)

		result = rt.maintain(
			memory_root=str(tmp_path / "memory"),
			workspace_root=str(tmp_path / "workspace"),
		)

		assert "cost_usd" in result
		assert isinstance(result["run_folder"], str)

	def test_maintain_writes_artifacts(self, tmp_path, monkeypatch):
		"""maintain() writes agent_log and agent_trace.json."""
		rt, _ = _build_runtime(tmp_path, monkeypatch)
		(tmp_path / "memory").mkdir(exist_ok=True)

		pred = _make_prediction(
			completion_summary="Maintenance done.",
			trajectory={
				"thought_0": "Scanning memories",
				"tool_name_0": "scan",
				"tool_args_0": {},
				"observation_0": "3 memories found",
			},
		)
		monkeypatch.setattr(
			"lerim.server.runtime.MaintainAgent",
			lambda **kw: MagicMock(return_value=pred),
		)
		mock_span = MagicMock()
		mock_span.__enter__ = MagicMock(return_value=mock_span)
		mock_span.__exit__ = MagicMock(return_value=False)
		monkeypatch.setattr("lerim.server.runtime.logfire.span", lambda *a, **kw: mock_span)

		result = rt.maintain(
			memory_root=str(tmp_path / "memory"),
			workspace_root=str(tmp_path / "workspace"),
		)

		run_folder = Path(result["run_folder"])
		assert (run_folder / "agent.log").exists()
		assert (run_folder / "agent_trace.json").exists()

	def test_maintain_handles_agent_failure(self, tmp_path, monkeypatch):
		"""maintain() propagates agent exceptions after cleaning up cost tracking."""
		monkeypatch.setattr(time, "sleep", lambda _: None)
		rt, _ = _build_runtime(tmp_path, monkeypatch)
		(tmp_path / "memory").mkdir(exist_ok=True)

		monkeypatch.setattr(
			"lerim.server.runtime.MaintainAgent",
			lambda **kw: MagicMock(side_effect=RuntimeError("LLM failed")),
		)
		mock_span = MagicMock()
		mock_span.__enter__ = MagicMock(return_value=mock_span)
		mock_span.__exit__ = MagicMock(return_value=False)
		monkeypatch.setattr("lerim.server.runtime.logfire.span", lambda *a, **kw: mock_span)

		with pytest.raises(RuntimeError, match="Failed after trying"):
			rt.maintain(
				memory_root=str(tmp_path / "memory"),
				workspace_root=str(tmp_path / "workspace"),
			)


# ---------------------------------------------------------------------------
# Ask flow
# ---------------------------------------------------------------------------


class TestAskFlow:
	"""Tests for ask() method."""

	def test_ask_happy_path(self, tmp_path, monkeypatch):
		"""ask() returns (answer, session_id, cost_usd) tuple."""
		rt, _ = _build_runtime(tmp_path, monkeypatch)

		pred = _make_prediction(answer="The auth pattern uses JWT tokens.")
		monkeypatch.setattr(
			"lerim.server.runtime.AskAgent",
			lambda **kw: MagicMock(return_value=pred),
		)
		monkeypatch.setattr(
			"lerim.server.runtime.format_ask_hints", lambda **kw: ""
		)

		answer, session_id, cost = rt.ask("What auth pattern do we use?")
		assert answer == "The auth pattern uses JWT tokens."
		assert session_id.startswith("lerim-")
		assert cost >= 0.0

	def test_ask_custom_session_id(self, tmp_path, monkeypatch):
		"""ask() uses provided session_id instead of generating one."""
		rt, _ = _build_runtime(tmp_path, monkeypatch)

		pred = _make_prediction(answer="Yes.")
		monkeypatch.setattr(
			"lerim.server.runtime.AskAgent",
			lambda **kw: MagicMock(return_value=pred),
		)
		monkeypatch.setattr(
			"lerim.server.runtime.format_ask_hints", lambda **kw: ""
		)

		_, session_id, _ = rt.ask("question?", session_id="my-custom-id")
		assert session_id == "my-custom-id"

	def test_ask_empty_answer(self, tmp_path, monkeypatch):
		"""ask() returns '(no response)' when agent returns empty answer."""
		rt, _ = _build_runtime(tmp_path, monkeypatch)

		pred = _make_prediction(answer="")
		monkeypatch.setattr(
			"lerim.server.runtime.AskAgent",
			lambda **kw: MagicMock(return_value=pred),
		)
		monkeypatch.setattr(
			"lerim.server.runtime.format_ask_hints", lambda **kw: ""
		)

		answer, _, _ = rt.ask("anything")
		assert answer == "(no response)"

	def test_ask_propagates_error(self, tmp_path, monkeypatch):
		"""ask() raises when agent fails after all retries."""
		monkeypatch.setattr(time, "sleep", lambda _: None)
		rt, _ = _build_runtime(tmp_path, monkeypatch)

		monkeypatch.setattr(
			"lerim.server.runtime.AskAgent",
			lambda **kw: MagicMock(side_effect=RuntimeError("model down")),
		)
		monkeypatch.setattr(
			"lerim.server.runtime.format_ask_hints", lambda **kw: ""
		)

		with pytest.raises(RuntimeError, match="Failed after trying"):
			rt.ask("question?")
