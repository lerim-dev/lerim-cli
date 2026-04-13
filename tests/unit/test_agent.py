"""Unit tests for ask/maintain agents and runtime helpers (PydanticAI-only)."""

from __future__ import annotations

from dataclasses import replace
import httpx
import pytest
from openai import RateLimitError

from lerim.agents.ask import ASK_SYSTEM_PROMPT, AskResult, format_ask_hints, run_ask
from lerim.agents.maintain import MAINTAIN_SYSTEM_PROMPT, MaintainResult, run_maintain
from lerim.config.settings import RoleConfig
from lerim.server.runtime import (
	LerimRuntime,
	_is_quota_error_pydantic,
	build_maintain_artifact_paths,
)
from tests.helpers import make_config


def _make_rate_limit_error() -> RateLimitError:
	"""Build a real OpenAI RateLimitError for isinstance-based quota tests."""
	return RateLimitError(
		message="rate limited",
		response=httpx.Response(
			429,
			request=httpx.Request("POST", "https://test.local"),
		),
		body=None,
	)


def test_ask_system_prompt_mentions_required_tools_and_layout() -> None:
	"""Ask prompt should guide scan/read behavior and memory layout."""
	assert "scan()" in ASK_SYSTEM_PROMPT
	assert "read()" in ASK_SYSTEM_PROMPT
	assert "summaries" in ASK_SYSTEM_PROMPT
	assert "feedback_" in ASK_SYSTEM_PROMPT


def test_maintain_system_prompt_mentions_write_archive_and_verify() -> None:
	"""Maintain prompt should include core mutation and validation steps."""
	assert "write()" in MAINTAIN_SYSTEM_PROMPT
	assert "archive()" in MAINTAIN_SYSTEM_PROMPT
	assert "verify_index()" in MAINTAIN_SYSTEM_PROMPT


def test_format_ask_hints_renders_hits_and_context_docs() -> None:
	"""Hints formatter should include both pre-fetched hits and context docs."""
	hints = format_ask_hints(
		hits=[
			{
				"type": "project",
				"name": "Auth",
				"description": "JWT pattern",
				"body": "Use short-lived access tokens and rotate refresh tokens.",
			},
		],
		context_docs=[
			{
				"doc_id": "doc-1",
				"title": "Login Flow",
				"body": "Read this first when changing auth.",
			},
		],
	)
	assert "Auth" in hints
	assert "doc-1" in hints
	assert "Login Flow" in hints


def test_format_ask_hints_empty_has_placeholders() -> None:
	"""Empty inputs should still produce explicit placeholder sections."""
	hints = format_ask_hints(hits=[], context_docs=[])
	assert "no relevant memories" in hints
	assert "no context docs loaded" in hints


def test_run_ask_delegates_to_built_agent(monkeypatch, tmp_path) -> None:
	"""run_ask should pass prompt/deps/limits and return AskResult output."""
	captured: dict[str, object] = {}

	class _FakeRunResult:
		def __init__(self) -> None:
			self.output = AskResult(answer="answer with citations")

		def all_messages(self):
			return []

	class _FakeAgent:
		def run_sync(self, prompt, *, deps, usage_limits):
			captured["prompt"] = prompt
			captured["deps"] = deps
			captured["request_limit"] = usage_limits.request_limit
			return _FakeRunResult()

	monkeypatch.setattr("lerim.agents.ask.build_ask_agent", lambda _model: _FakeAgent())
	result = run_ask(
		memory_root=tmp_path,
		model=object(),
		question="What changed?",
		hints="hint block",
		request_limit=7,
	)
	assert result.answer == "answer with citations"
	assert "What changed?" in str(captured["prompt"])
	assert "hint block" in str(captured["prompt"])
	assert captured["request_limit"] == 7


def test_run_maintain_delegates_to_built_agent(monkeypatch, tmp_path) -> None:
	"""run_maintain should pass deps/limits and return MaintainResult output."""
	captured: dict[str, object] = {}

	class _FakeRunResult:
		def __init__(self) -> None:
			self.output = MaintainResult(completion_summary="merged 2")

		def all_messages(self):
			return []

	class _FakeAgent:
		def run_sync(self, prompt, *, deps, usage_limits):
			captured["prompt"] = prompt
			captured["deps"] = deps
			captured["request_limit"] = usage_limits.request_limit
			return _FakeRunResult()

	monkeypatch.setattr(
		"lerim.agents.maintain.build_maintain_agent", lambda _model: _FakeAgent()
	)
	result = run_maintain(memory_root=tmp_path, model=object(), request_limit=9)
	assert result.completion_summary == "merged 2"
	assert "Maintain the memory store" in str(captured["prompt"])
	assert captured["request_limit"] == 9


def test_runtime_init_and_missing_trace(tmp_path, monkeypatch) -> None:
	"""Runtime should initialize and keep missing trace behavior unchanged."""
	cfg = replace(make_config(tmp_path), openrouter_api_key="test-key")
	monkeypatch.setattr(
		"lerim.config.providers.validate_provider_for_role",
		lambda *args, **kwargs: None,
	)
	runtime = LerimRuntime(default_cwd=str(tmp_path), config=cfg)
	with pytest.raises(FileNotFoundError, match="trace_path_missing"):
		runtime.sync(trace_path=tmp_path / "missing.jsonl")


def test_runtime_generate_session_id_is_unique() -> None:
	"""Session IDs should have the expected prefix and be unique."""
	sid1 = LerimRuntime.generate_session_id()
	sid2 = LerimRuntime.generate_session_id()
	assert sid1.startswith("lerim-")
	assert sid2.startswith("lerim-")
	assert sid1 != sid2


def test_build_maintain_artifact_paths_keys(tmp_path) -> None:
	"""Maintain artifact helper should expose only expected keys."""
	paths = build_maintain_artifact_paths(tmp_path / "run")
	assert set(paths.keys()) == {"agent_log", "subagents_log"}


def test_is_quota_error_pydantic_detection() -> None:
	"""Quota/rate-limit classification should catch both typed and string errors."""
	assert _is_quota_error_pydantic(_make_rate_limit_error())
	assert _is_quota_error_pydantic(RuntimeError("HTTP 429 Too Many Requests"))
	assert _is_quota_error_pydantic(RuntimeError("quota exceeded"))
	assert not _is_quota_error_pydantic(RuntimeError("connection reset"))


def test_runtime_accepts_role_config_limits(tmp_path, monkeypatch) -> None:
	"""Role request limits should be read from config object."""
	cfg = make_config(tmp_path)
	cfg = replace(
		cfg,
		agent_role=RoleConfig(
			provider="openrouter",
			model="x-ai/grok-4.1-fast",
			max_iters_maintain=12,
			max_iters_ask=8,
		),
		openrouter_api_key="test-key",
	)
	monkeypatch.setattr(
		"lerim.config.providers.validate_provider_for_role",
		lambda *args, **kwargs: None,
	)
	rt = LerimRuntime(default_cwd=str(tmp_path), config=cfg)
	assert rt.config.agent_role.max_iters_maintain == 12
	assert rt.config.agent_role.max_iters_ask == 8
