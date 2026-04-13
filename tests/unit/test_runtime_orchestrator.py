"""Unit tests for LerimRuntime orchestration (PydanticAI-only)."""

from __future__ import annotations

import json
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from openai import RateLimitError
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import ModelRequest, SystemPromptPart

from lerim.agents.ask import AskResult
from lerim.agents.extract import ExtractionResult
from lerim.server.runtime import (
	LerimRuntime,
	_default_run_folder_name,
	_resolve_runtime_roots,
	_write_agent_trace,
	_write_json_artifact,
	_write_text_with_newline,
)
from tests.helpers import make_config


def _make_rate_limit_error() -> RateLimitError:
	"""Build a real OpenAI RateLimitError for retry/fallback tests."""
	return RateLimitError(
		message="rate limited",
		response=httpx.Response(
			429,
			request=httpx.Request("POST", "https://test.local"),
		),
		body=None,
	)


def _build_runtime(tmp_path, monkeypatch):
	"""Construct runtime with provider validation mocked."""
	cfg = replace(make_config(tmp_path), openrouter_api_key="test-key")
	monkeypatch.setattr(
		"lerim.config.providers.validate_provider_for_role",
		lambda *args, **kwargs: None,
	)
	return LerimRuntime(default_cwd=str(tmp_path), config=cfg)


class TestHelpers:
	def test_default_run_folder_name(self):
		name = _default_run_folder_name("sync")
		assert name.startswith("sync-")
		assert len(name.split("-")) >= 3

	def test_resolve_runtime_roots_defaults(self, tmp_path):
		cfg = make_config(tmp_path)
		mem, ws = _resolve_runtime_roots(
			config=cfg,
			memory_root=None,
			workspace_root=None,
		)
		assert mem == cfg.memory_dir
		assert ws == cfg.global_data_dir / "workspace"

	def test_resolve_runtime_roots_overrides(self, tmp_path):
		cfg = make_config(tmp_path)
		mem, ws = _resolve_runtime_roots(
			config=cfg,
			memory_root=str(tmp_path / "m"),
			workspace_root=str(tmp_path / "w"),
		)
		assert mem == (tmp_path / "m").resolve()
		assert ws == (tmp_path / "w").resolve()

	def test_write_json_artifact(self, tmp_path):
		path = tmp_path / "artifact.json"
		_write_json_artifact(path, {"k": "v"})
		text = path.read_text(encoding="utf-8")
		assert text.endswith("\n")
		assert json.loads(text) == {"k": "v"}

	def test_write_text_with_newline(self, tmp_path):
		path = tmp_path / "artifact.log"
		_write_text_with_newline(path, "hello")
		assert path.read_text(encoding="utf-8") == "hello\n"

	def test_write_agent_trace_serializes_messages(self, tmp_path):
		path = tmp_path / "agent_trace.json"
		messages = [ModelRequest(parts=[SystemPromptPart(content="system")])]
		_write_agent_trace(path, messages)
		data = json.loads(path.read_text(encoding="utf-8"))
		assert isinstance(data, list)
		assert len(data) == 1


class TestRunWithFallback:
	def test_success_primary(self, tmp_path, monkeypatch):
		rt = _build_runtime(tmp_path, monkeypatch)
		seen = []

		def call(model):
			seen.append(model)
			return "ok"

		result = rt._run_with_fallback(
			flow="test",
			callable_fn=call,
			model_builders=[lambda: "primary", lambda: "fallback"],
		)
		assert result == "ok"
		assert seen == ["primary"]

	def test_retry_transient_error_same_model(self, tmp_path, monkeypatch):
		monkeypatch.setattr(time, "sleep", lambda *_: None)
		rt = _build_runtime(tmp_path, monkeypatch)
		attempts = 0

		def call(_model):
			nonlocal attempts
			attempts += 1
			if attempts < 3:
				raise RuntimeError("500 temporary")
			return "recovered"

		result = rt._run_with_fallback(
			flow="test",
			callable_fn=call,
			model_builders=[lambda: "primary"],
			max_attempts=3,
		)
		assert result == "recovered"
		assert attempts == 3

	def test_quota_switches_to_fallback(self, tmp_path, monkeypatch):
		monkeypatch.setattr(time, "sleep", lambda *_: None)
		rt = _build_runtime(tmp_path, monkeypatch)
		seen = []

		def call(model):
			seen.append(model)
			if model == "primary":
				raise _make_rate_limit_error()
			return "fallback-ok"

		result = rt._run_with_fallback(
			flow="test",
			callable_fn=call,
			model_builders=[lambda: "primary", lambda: "fallback"],
		)
		assert result == "fallback-ok"
		assert seen == ["primary", "fallback"]

	def test_usage_limit_short_circuit(self, tmp_path, monkeypatch):
		rt = _build_runtime(tmp_path, monkeypatch)
		count = 0

		def call(_model):
			nonlocal count
			count += 1
			raise UsageLimitExceeded("request_limit")

		with pytest.raises(UsageLimitExceeded):
			rt._run_with_fallback(
				flow="test",
				callable_fn=call,
				model_builders=[lambda: "primary", lambda: "fallback"],
			)
		assert count == 1

	def test_exhausted_models_raises_runtime_error(self, tmp_path, monkeypatch):
		monkeypatch.setattr(time, "sleep", lambda *_: None)
		rt = _build_runtime(tmp_path, monkeypatch)

		def call(_model):
			raise RuntimeError("still broken")

		with pytest.raises(RuntimeError, match="Failed after trying"):
			rt._run_with_fallback(
				flow="test",
				callable_fn=call,
				model_builders=[lambda: "primary"],
				max_attempts=2,
			)


class TestSyncFlow:
	def test_sync_missing_trace_file(self, tmp_path, monkeypatch):
		rt = _build_runtime(tmp_path, monkeypatch)
		with pytest.raises(FileNotFoundError, match="trace_path_missing"):
			rt.sync(trace_path=tmp_path / "missing.jsonl")

	def test_sync_happy_path(self, tmp_path, monkeypatch):
		rt = _build_runtime(tmp_path, monkeypatch)
		memory_root = tmp_path / "memory"
		workspace_root = tmp_path / "workspace"
		memory_root.mkdir(parents=True, exist_ok=True)

		trace = tmp_path / "trace.jsonl"
		trace.write_text('{"role":"user","content":"hello"}\n', encoding="utf-8")

		monkeypatch.setattr(
			"lerim.server.runtime.build_pydantic_model",
			lambda *args, **kwargs: "fake-model",
		)
		monkeypatch.setattr(
			"lerim.server.runtime.run_extraction",
			lambda **kwargs: ExtractionResult(completion_summary="extracted"),
		)

		result = rt.sync(
			trace_path=trace,
			memory_root=memory_root,
			workspace_root=workspace_root,
		)

		run_folder = Path(result["run_folder"])
		assert run_folder.exists()
		assert (run_folder / "agent.log").read_text(encoding="utf-8").strip() == "extracted"
		assert json.loads((run_folder / "agent_trace.json").read_text(encoding="utf-8")) == []
		assert result["trace_path"] == str(trace.resolve())


class TestMaintainFlow:
	def test_maintain_happy_path_and_trace_write(self, tmp_path, monkeypatch):
		rt = _build_runtime(tmp_path, monkeypatch)
		memory_root = tmp_path / "memory"
		workspace_root = tmp_path / "workspace"
		memory_root.mkdir(parents=True, exist_ok=True)

		captured: dict[str, object] = {}

		monkeypatch.setattr(
			"lerim.server.runtime.build_pydantic_model",
			lambda *args, **kwargs: "fake-model",
		)

		def _fake_run_maintain(**kwargs):
			captured["request_limit"] = kwargs["request_limit"]
			return (
				SimpleNamespace(completion_summary="maintenance complete"),
				[ModelRequest(parts=[SystemPromptPart(content="maintain")])],
			)

		monkeypatch.setattr("lerim.server.runtime.run_maintain", _fake_run_maintain)

		result = rt.maintain(memory_root=memory_root, workspace_root=workspace_root)
		run_folder = Path(result["run_folder"])
		assert (run_folder / "agent.log").read_text(encoding="utf-8").strip() == "maintenance complete"
		trace_data = json.loads((run_folder / "agent_trace.json").read_text(encoding="utf-8"))
		assert isinstance(trace_data, list)
		assert captured["request_limit"] == rt.config.agent_role.max_iters_maintain


class TestAskFlow:
	def test_ask_happy_path(self, tmp_path, monkeypatch):
		rt = _build_runtime(tmp_path, monkeypatch)
		captured: dict[str, object] = {}
		monkeypatch.setattr(
			"lerim.server.runtime.build_pydantic_model",
			lambda *args, **kwargs: "fake-model",
		)

		def _fake_run_ask(**kwargs):
			captured["request_limit"] = kwargs["request_limit"]
			captured["question"] = kwargs["question"]
			return AskResult(answer="answer text")

		monkeypatch.setattr("lerim.server.runtime.run_ask", _fake_run_ask)
		answer, session_id, cost = rt.ask("what changed?")
		assert answer == "answer text"
		assert session_id.startswith("lerim-")
		assert cost == 0.0
		assert captured["question"] == "what changed?"
		assert captured["request_limit"] == rt.config.agent_role.max_iters_ask

	def test_ask_uses_provided_session_id(self, tmp_path, monkeypatch):
		rt = _build_runtime(tmp_path, monkeypatch)
		monkeypatch.setattr(
			"lerim.server.runtime.build_pydantic_model",
			lambda *args, **kwargs: "fake-model",
		)
		monkeypatch.setattr(
			"lerim.server.runtime.run_ask",
			lambda **kwargs: AskResult(answer="ok"),
		)
		_, session_id, _ = rt.ask("hello", session_id="fixed-id")
		assert session_id == "fixed-id"
