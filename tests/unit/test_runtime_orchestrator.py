"""Unit tests for LerimRuntime (PydanticAI sync + DSPy maintain/ask).

Tests sync, maintain, and ask flows with all LLM calls mocked. Covers
retry/fallback logic (DSPy and PydanticAI variants), trajectory conversion,
and artifact writing.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock

import dspy
import pytest

from lerim.agents.extract import ExtractionResult
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
# _run_dspy_with_fallback (maintain / ask path)
# ---------------------------------------------------------------------------


class TestRunDSPyWithFallback:
	"""Tests for the DSPy retry + fallback path used by maintain and ask."""

	def test_success_first_attempt(self, tmp_path, monkeypatch):
		"""Module succeeds on first attempt -- no retries."""
		rt, _ = _build_runtime(tmp_path, monkeypatch)
		pred = _make_prediction()

		mock_module = MagicMock(return_value=pred)
		result = rt._run_dspy_with_fallback(
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
		result = rt._run_dspy_with_fallback(
			flow="test", module=mock_module, input_args={}, max_attempts=3
		)
		assert result is pred
		assert call_count == 3

	def test_quota_error_switches_to_fallback(self, tmp_path, monkeypatch):
		"""Quota error on primary model switches to fallback model."""
		from dataclasses import replace

		monkeypatch.setattr(time, "sleep", lambda _: None)

		cfg = make_config(tmp_path)
		cfg = replace(cfg, agent_role=replace(
			cfg.agent_role, fallback_models=("fallback/model-1",)
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
		result = rt._run_dspy_with_fallback(
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
			rt._run_dspy_with_fallback(
				flow="test", module=mock_module, input_args={}, max_attempts=2
			)


# ---------------------------------------------------------------------------
# Sync flow
# ---------------------------------------------------------------------------


def _patch_pydantic_sync(monkeypatch, *, result: ExtractionResult):
	"""Replace the PydanticAI sync callable + model builder with stubs.

	The runtime's `sync()` flow calls `build_pydantic_model(...)` and
	`run_extraction(...)`. For unit tests we swap both so nothing touches
	a real provider.
	"""
	# Signature mirrors the real lerim.config.providers.build_pydantic_model
	# which takes (role, *, config=None). Runtime.sync calls it as
	# build_pydantic_model("agent", config=self.config).
	monkeypatch.setattr(
		"lerim.server.runtime.build_pydantic_model",
		lambda role, *, config=None: f"fake-model-{role}",
	)

	def fake_run_extraction(
		memory_root,
		trace_path,
		model,
		run_folder=None,
		return_messages=False,
	):
		"""Stub runner that returns a deterministic ExtractionResult.

		Signature mirrors the real lerim.agents.extract.run_extraction — no
		per-pass budget kwargs (the real runner auto-scales the budget
		internally via compute_request_budget).
		"""
		if return_messages:
			return result, []
		return result

	monkeypatch.setattr(
		"lerim.server.runtime.run_extraction", fake_run_extraction
	)


class TestSyncFlow:
	"""Tests for sync() and _sync_inner()."""

	def test_sync_missing_trace_file(self, tmp_path, monkeypatch):
		"""sync() raises FileNotFoundError for non-existent trace file."""
		rt, _ = _build_runtime(tmp_path, monkeypatch)
		with pytest.raises(FileNotFoundError, match="trace_path_missing"):
			rt.sync(trace_path=tmp_path / "nonexistent.jsonl")

	def test_sync_happy_path(self, tmp_path, monkeypatch):
		"""sync() returns validated SyncResultContract payload on success."""
		rt, _ = _build_runtime(tmp_path, monkeypatch)
		(tmp_path / "memory").mkdir(exist_ok=True)

		# Create trace file
		trace_file = tmp_path / "trace.jsonl"
		trace_file.write_text('{"type": "test"}\n', encoding="utf-8")

		_patch_pydantic_sync(
			monkeypatch,
			result=ExtractionResult(
				completion_summary="Extracted 2 memories.",
			),
		)

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

		_patch_pydantic_sync(
			monkeypatch,
			result=ExtractionResult(
				completion_summary="Done.",
			),
		)

		result = rt.sync(
			trace_path=trace_file,
			memory_root=str(tmp_path / "memory"),
			workspace_root=str(tmp_path / "workspace"),
		)

		run_folder = Path(result["run_folder"])
		assert (run_folder / "agent.log").exists()
		assert (run_folder / "agent_trace.json").exists()

		# agent.log should carry the completion summary.
		log_content = (run_folder / "agent.log").read_text(encoding="utf-8")
		assert "Done." in log_content

		# agent_trace.json is a placeholder JSON list in Phase 2.
		trace_data = json.loads(
			(run_folder / "agent_trace.json").read_text(encoding="utf-8")
		)
		assert isinstance(trace_data, list)


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

		result = rt.maintain(
			memory_root=str(tmp_path / "memory"),
			workspace_root=str(tmp_path / "workspace"),
		)

		run_folder = Path(result["run_folder"])
		assert (run_folder / "agent.log").exists()
		assert (run_folder / "agent_trace.json").exists()

	def test_maintain_handles_agent_failure(self, tmp_path, monkeypatch):
		"""maintain() propagates agent exceptions after all retries exhausted."""
		monkeypatch.setattr(time, "sleep", lambda _: None)
		rt, _ = _build_runtime(tmp_path, monkeypatch)
		(tmp_path / "memory").mkdir(exist_ok=True)

		monkeypatch.setattr(
			"lerim.server.runtime.MaintainAgent",
			lambda **kw: MagicMock(side_effect=RuntimeError("LLM failed")),
		)

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
