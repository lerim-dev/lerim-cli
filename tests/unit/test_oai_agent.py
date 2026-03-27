"""Unit tests for LerimOAIAgent sync and maintain flows."""

from __future__ import annotations

from dataclasses import replace

import pytest

from lerim.config.settings import LLMRoleConfig
from lerim.runtime.oai_agent import LerimOAIAgent
from lerim.runtime.prompts.oai_ask import build_oai_ask_prompt
from lerim.runtime.prompts.oai_maintain import (
	build_oai_maintain_artifact_paths,
	build_oai_maintain_prompt,
)
from lerim.runtime.prompts.oai_sync import build_oai_sync_prompt
from tests.helpers import make_config


# ---------------------------------------------------------------------------
# Prompt builder tests (no LLM calls)
# ---------------------------------------------------------------------------


def test_oai_sync_prompt_contains_steps(tmp_path):
	"""Sync prompt should contain all 4 steps (batch dedup flow)."""
	trace = tmp_path / "trace.jsonl"
	trace.write_text('{"role":"user","content":"hello"}\n')
	memory_root = tmp_path / "memory"
	run_folder = tmp_path / "workspace" / "sync-test"
	artifact_paths = {
		"extract": run_folder / "extract.json",
		"summary": run_folder / "summary.json",
		"memory_actions": run_folder / "memory_actions.json",
		"agent_log": run_folder / "agent.log",
		"subagents_log": run_folder / "subagents.log",
		"session_log": run_folder / "session.log",
	}
	prompt = build_oai_sync_prompt(
		trace_file=trace,
		memory_root=memory_root,
		run_folder=run_folder,
		artifact_paths=artifact_paths,
		metadata={"run_id": "sync-test", "trace_path": str(trace), "repo_name": "lerim"},
	)
	assert "EXTRACT + SUMMARIZE" in prompt
	assert "BATCH DEDUP" in prompt
	assert "batch_dedup_candidates" in prompt
	assert "CLASSIFY AND WRITE" in prompt
	assert "WRITE REPORT" in prompt


def test_oai_sync_prompt_references_lightweight_tools(tmp_path):
	"""Sync prompt should reference lightweight tools for simple ops, not bare codex."""
	trace = tmp_path / "trace.jsonl"
	trace.write_text('{"role":"user","content":"hello"}\n')
	prompt = build_oai_sync_prompt(
		trace_file=trace,
		memory_root=tmp_path / "memory",
		run_folder=tmp_path / "workspace" / "sync-test",
		artifact_paths={
			"extract": tmp_path / "extract.json",
			"summary": tmp_path / "summary.json",
			"memory_actions": tmp_path / "memory_actions.json",
			"agent_log": tmp_path / "agent.log",
			"subagents_log": tmp_path / "subagents.log",
			"session_log": tmp_path / "session.log",
		},
		metadata={"run_id": "test"},
	)
	# Lightweight tools referenced for simple ops
	assert "read_file" in prompt
	assert "write_report" in prompt
	assert "write_memory" in prompt
	assert "extract_pipeline" in prompt
	assert "summarize_pipeline" in prompt
	assert "batch_dedup_candidates" in prompt
	# Should NOT reference old PydanticAI tools
	assert "explore()" not in prompt


def test_oai_sync_prompt_contains_artifact_paths(tmp_path):
	"""Sync prompt should embed artifact paths."""
	trace = tmp_path / "trace.jsonl"
	trace.write_text('{"role":"user","content":"hello"}\n')
	extract_path = tmp_path / "extract.json"
	prompt = build_oai_sync_prompt(
		trace_file=trace,
		memory_root=tmp_path / "memory",
		run_folder=tmp_path / "workspace",
		artifact_paths={
			"extract": extract_path,
			"summary": tmp_path / "summary.json",
			"memory_actions": tmp_path / "memory_actions.json",
			"agent_log": tmp_path / "agent.log",
			"subagents_log": tmp_path / "subagents.log",
			"session_log": tmp_path / "session.log",
		},
		metadata={"run_id": "test"},
	)
	assert str(extract_path) in prompt


def test_oai_sync_prompt_contains_decision_policy(tmp_path):
	"""Sync prompt should describe the no_op/update/add decision policy."""
	trace = tmp_path / "trace.jsonl"
	trace.write_text('{"role":"user","content":"hello"}\n')
	prompt = build_oai_sync_prompt(
		trace_file=trace,
		memory_root=tmp_path / "memory",
		run_folder=tmp_path / "workspace",
		artifact_paths={
			"extract": tmp_path / "e.json",
			"summary": tmp_path / "s.json",
			"memory_actions": tmp_path / "m.json",
			"agent_log": tmp_path / "a.log",
			"subagents_log": tmp_path / "sub.log",
			"session_log": tmp_path / "sess.log",
		},
		metadata={"run_id": "test"},
	)
	assert "no_op" in prompt
	assert "update" in prompt
	assert "similarity" in prompt.lower()


# ---------------------------------------------------------------------------
# Agent construction tests (no LLM calls)
# ---------------------------------------------------------------------------


def test_oai_agent_init_minimax(tmp_path):
	"""LerimOAIAgent with MiniMax provider should need a proxy."""
	cfg = make_config(tmp_path)
	minimax_role = LLMRoleConfig(
		provider="minimax",
		model="MiniMax-M2.5",
		api_base="",
		fallback_models=(),
		timeout_seconds=120,
		max_iterations=10,
		openrouter_provider_order=(),
	)
	cfg = replace(cfg, lead_role=minimax_role, minimax_api_key="test-key")
	agent = LerimOAIAgent(default_cwd=str(tmp_path), config=cfg)
	assert agent.config is cfg
	assert agent._lead_model is not None
	assert agent._lead_model is not None


def test_oai_agent_init_builds_fallback_models(tmp_path):
	"""LerimOAIAgent should build fallback models from config."""
	cfg = make_config(tmp_path)
	role = LLMRoleConfig(
		provider="minimax",
		model="MiniMax-M2.5",
		api_base="",
		fallback_models=("openrouter:qwen/qwen3-coder",),
		timeout_seconds=120,
		max_iterations=10,
		openrouter_provider_order=(),
	)
	cfg = replace(cfg, lead_role=role, minimax_api_key="test-key", openrouter_api_key="test-key")
	agent = LerimOAIAgent(default_cwd=str(tmp_path), config=cfg)
	assert len(agent._fallback_models) == 1


def test_oai_agent_init_no_fallback_models(tmp_path):
	"""LerimOAIAgent with no fallbacks configured should have empty list."""
	cfg = make_config(tmp_path)
	agent = LerimOAIAgent(default_cwd=str(tmp_path), config=cfg)
	assert agent._fallback_models == []


def test_oai_agent_init_openai_no_proxy(tmp_path):
	"""OpenAI provider for both lead and codex should not need a proxy."""
	from lerim.config.settings import CodexRoleConfig
	cfg = make_config(tmp_path)
	openai_role = LLMRoleConfig(
		provider="openai",
		model="gpt-5-mini",
		api_base="",
		fallback_models=(),
		timeout_seconds=120,
		max_iterations=10,
		openrouter_provider_order=(),
	)
	openai_codex = CodexRoleConfig(provider="openai", model="gpt-5-mini")
	cfg = replace(cfg, lead_role=openai_role, codex_role=openai_codex, openai_api_key="test-key")
	agent = LerimOAIAgent(default_cwd=str(tmp_path), config=cfg)
	assert agent._lead_model is not None


def test_oai_agent_sync_missing_trace(tmp_path):
	"""sync() should raise FileNotFoundError for missing trace."""
	cfg = make_config(tmp_path)
	cfg = replace(cfg, minimax_api_key="test-key")
	agent = LerimOAIAgent(default_cwd=str(tmp_path), config=cfg)
	with pytest.raises(FileNotFoundError, match="trace_path_missing"):
		agent.sync(tmp_path / "nonexistent.jsonl")


# ---------------------------------------------------------------------------
# Quota error detection tests
# ---------------------------------------------------------------------------


def test_is_quota_error_429():
	"""_is_quota_error should detect HTTP 429 status codes."""
	assert LerimOAIAgent._is_quota_error("Error 429: Too Many Requests")


def test_is_quota_error_rate_limit():
	"""_is_quota_error should detect 'rate limit' text (case-insensitive)."""
	assert LerimOAIAgent._is_quota_error("Rate Limit exceeded for model")


def test_is_quota_error_quota():
	"""_is_quota_error should detect 'quota' text (case-insensitive)."""
	assert LerimOAIAgent._is_quota_error("Quota exceeded for this billing period")


def test_is_quota_error_negative():
	"""_is_quota_error should return False for non-quota errors."""
	assert not LerimOAIAgent._is_quota_error("Internal server error 500")
	assert not LerimOAIAgent._is_quota_error("Connection timeout")


# ---------------------------------------------------------------------------
# Fallback retry logic tests (mocked Runner, no LLM calls)
# ---------------------------------------------------------------------------


def test_run_with_fallback_succeeds_on_primary(tmp_path, monkeypatch):
	"""_run_with_fallback should return on first success without trying fallbacks."""
	cfg = make_config(tmp_path)
	role = LLMRoleConfig(
		provider="minimax",
		model="MiniMax-M2.5",
		api_base="",
		fallback_models=("openrouter:qwen/qwen3-coder",),
		timeout_seconds=120,
		max_iterations=10,
		openrouter_provider_order=(),
	)
	cfg = replace(cfg, lead_role=role, minimax_api_key="test-key", openrouter_api_key="test-key")
	agent_obj = LerimOAIAgent(default_cwd=str(tmp_path), config=cfg)

	class FakeResult:
		final_output = "success"

	call_count = 0

	def fake_run_async(coro):
		nonlocal call_count
		call_count += 1
		return FakeResult()

	import lerim.runtime.oai_agent as oai_mod
	monkeypatch.setattr(oai_mod, "_run_async", fake_run_async)

	# Minimal build_agent_fn that just returns a sentinel
	result = agent_obj._run_with_fallback(
		flow="test",
		build_agent_fn=lambda model: "fake-agent",
		prompt="test prompt",
		ctx=None,
		max_turns=5,
	)
	assert result.final_output == "success"
	assert call_count == 1  # Only one call, no fallback needed


def test_run_with_fallback_switches_on_quota_error(tmp_path, monkeypatch):
	"""_run_with_fallback should switch to fallback model on quota error."""
	cfg = make_config(tmp_path)
	role = LLMRoleConfig(
		provider="minimax",
		model="MiniMax-M2.5",
		api_base="",
		fallback_models=("openrouter:qwen/qwen3-coder",),
		timeout_seconds=120,
		max_iterations=10,
		openrouter_provider_order=(),
	)
	cfg = replace(cfg, lead_role=role, minimax_api_key="test-key", openrouter_api_key="test-key")
	agent_obj = LerimOAIAgent(default_cwd=str(tmp_path), config=cfg)

	class FakeResult:
		final_output = "fallback success"

	models_tried = []

	def fake_run_async(coro):
		# First call raises quota error, second succeeds
		if len(models_tried) == 0:
			models_tried.append("primary")
			raise RuntimeError("Error 429: Rate limit exceeded")
		models_tried.append("fallback")
		return FakeResult()

	import lerim.runtime.oai_agent as oai_mod
	monkeypatch.setattr(oai_mod, "_run_async", fake_run_async)
	monkeypatch.setattr(oai_mod.time, "sleep", lambda _: None)

	result = agent_obj._run_with_fallback(
		flow="test",
		build_agent_fn=lambda model: "fake-agent",
		prompt="test prompt",
		ctx=None,
		max_turns=5,
	)
	assert result.final_output == "fallback success"
	assert models_tried == ["primary", "fallback"]


def test_run_with_fallback_raises_when_all_exhausted(tmp_path, monkeypatch):
	"""_run_with_fallback should raise RuntimeError when all models fail."""
	cfg = make_config(tmp_path)
	role = LLMRoleConfig(
		provider="minimax",
		model="MiniMax-M2.5",
		api_base="",
		fallback_models=(),
		timeout_seconds=120,
		max_iterations=10,
		openrouter_provider_order=(),
	)
	cfg = replace(cfg, lead_role=role, minimax_api_key="test-key")
	agent_obj = LerimOAIAgent(default_cwd=str(tmp_path), config=cfg)

	def fake_run_async(coro):
		raise RuntimeError("Connection timeout")

	import lerim.runtime.oai_agent as oai_mod
	monkeypatch.setattr(oai_mod, "_run_async", fake_run_async)
	monkeypatch.setattr(oai_mod.time, "sleep", lambda _: None)

	with pytest.raises(RuntimeError, match="Failed after trying 1 model"):
		agent_obj._run_with_fallback(
			flow="test",
			build_agent_fn=lambda model: "fake-agent",
			prompt="test prompt",
			ctx=None,
			max_turns=5,
		)


def test_run_with_fallback_retries_same_model_on_non_quota_error(tmp_path, monkeypatch):
	"""_run_with_fallback should retry same model on non-quota errors with backoff."""
	cfg = make_config(tmp_path)
	agent_obj = LerimOAIAgent(default_cwd=str(tmp_path), config=cfg)

	class FakeResult:
		final_output = "recovered"

	attempt_count = 0

	def fake_run_async(coro):
		nonlocal attempt_count
		attempt_count += 1
		if attempt_count < 3:
			raise RuntimeError("Server error 500")
		return FakeResult()

	import lerim.runtime.oai_agent as oai_mod
	monkeypatch.setattr(oai_mod, "_run_async", fake_run_async)
	monkeypatch.setattr(oai_mod.time, "sleep", lambda _: None)

	result = agent_obj._run_with_fallback(
		flow="test",
		build_agent_fn=lambda model: "fake-agent",
		prompt="test prompt",
		ctx=None,
		max_turns=5,
	)
	assert result.final_output == "recovered"
	assert attempt_count == 3


# ---------------------------------------------------------------------------
# Maintain prompt tests (no LLM calls)
# ---------------------------------------------------------------------------


def _make_maintain_artifacts(tmp_path):
	"""Build maintain artifact paths for testing."""
	run_folder = tmp_path / "workspace" / "maintain-test"
	return run_folder, build_oai_maintain_artifact_paths(run_folder)


def test_oai_maintain_prompt_contains_steps(tmp_path):
	"""Maintain prompt should contain all 9 steps."""
	run_folder, artifact_paths = _make_maintain_artifacts(tmp_path)
	prompt = build_oai_maintain_prompt(
		memory_root=tmp_path / "memory",
		run_folder=run_folder,
		artifact_paths=artifact_paths,
	)
	assert "SCAN MEMORIES" in prompt
	assert "CROSS-SESSION ANALYSIS" in prompt
	assert "ANALYZE DUPLICATES" in prompt
	assert "MERGE" in prompt
	assert "ARCHIVE" in prompt
	assert "DECAY" in prompt
	assert "CONSOLIDATE" in prompt
	assert "HOT MEMORY" in prompt or "hot-memory" in prompt
	assert "REPORT" in prompt


def test_oai_maintain_prompt_cross_session_analysis(tmp_path):
	"""Maintain prompt should include signal, contradiction, and gap detection."""
	run_folder, artifact_paths = _make_maintain_artifacts(tmp_path)
	prompt = build_oai_maintain_prompt(
		memory_root=tmp_path / "memory",
		run_folder=run_folder,
		artifact_paths=artifact_paths,
	)
	assert "signal" in prompt.lower() or "amplif" in prompt.lower()
	assert "contradiction" in prompt.lower()
	assert "gap" in prompt.lower()


def test_oai_maintain_prompt_hot_memory_path(tmp_path):
	"""Maintain prompt should reference the hot-memory.md path."""
	memory_root = tmp_path / "memory"
	run_folder, artifact_paths = _make_maintain_artifacts(tmp_path)
	prompt = build_oai_maintain_prompt(
		memory_root=memory_root,
		run_folder=run_folder,
		artifact_paths=artifact_paths,
	)
	expected_hot_memory = str(memory_root.parent / "hot-memory.md")
	assert expected_hot_memory in prompt or "hot-memory.md" in prompt


def test_oai_maintain_prompt_no_explore_tool(tmp_path):
	"""Maintain prompt should NOT reference explore() tool — uses lightweight tools + codex."""
	run_folder, artifact_paths = _make_maintain_artifacts(tmp_path)
	prompt = build_oai_maintain_prompt(
		memory_root=tmp_path / "memory",
		run_folder=run_folder,
		artifact_paths=artifact_paths,
	)
	assert "explore()" not in prompt
	assert "codex" in prompt.lower()
	assert "list_files" in prompt
	assert "read_file" in prompt
	assert "write_report" in prompt


def test_oai_maintain_prompt_with_access_stats(tmp_path):
	"""Maintain prompt should include access stats when provided."""
	run_folder, artifact_paths = _make_maintain_artifacts(tmp_path)
	stats = [
		{"memory_id": "20260301-test", "last_accessed": "2026-03-01T10:00:00Z", "access_count": 5},
	]
	prompt = build_oai_maintain_prompt(
		memory_root=tmp_path / "memory",
		run_folder=run_folder,
		artifact_paths=artifact_paths,
		access_stats=stats,
	)
	assert "20260301-test" in prompt
	assert "DECAY POLICY" in prompt


def test_oai_maintain_prompt_without_access_stats(tmp_path):
	"""Maintain prompt without access stats should skip decay."""
	run_folder, artifact_paths = _make_maintain_artifacts(tmp_path)
	prompt = build_oai_maintain_prompt(
		memory_root=tmp_path / "memory",
		run_folder=run_folder,
		artifact_paths=artifact_paths,
		access_stats=None,
	)
	assert "No access data available" in prompt


def test_oai_maintain_artifact_paths(tmp_path):
	"""Maintain artifact paths should include standard keys."""
	run_folder = tmp_path / "workspace" / "maintain-test"
	paths = build_oai_maintain_artifact_paths(run_folder)
	assert "maintain_actions" in paths
	assert "agent_log" in paths
	assert "subagents_log" in paths


def test_oai_maintain_prompt_summaries_reference(tmp_path):
	"""Maintain prompt should instruct reading summaries for cross-session analysis."""
	run_folder, artifact_paths = _make_maintain_artifacts(tmp_path)
	prompt = build_oai_maintain_prompt(
		memory_root=tmp_path / "memory",
		run_folder=run_folder,
		artifact_paths=artifact_paths,
	)
	assert "summaries" in prompt.lower()


def test_oai_maintain_prompt_cross_agent_patterns(tmp_path):
	"""Maintain prompt should include cross-agent pattern detection."""
	run_folder, artifact_paths = _make_maintain_artifacts(tmp_path)
	prompt = build_oai_maintain_prompt(
		memory_root=tmp_path / "memory",
		run_folder=run_folder,
		artifact_paths=artifact_paths,
	)
	assert "Cross-Agent Patterns" in prompt
	assert "coding_agent" in prompt
	assert "cross_agent" in prompt


def test_oai_maintain_prompt_hot_memory_cross_agent_section(tmp_path):
	"""Hot-memory template should include Cross-Agent Insights section."""
	run_folder, artifact_paths = _make_maintain_artifacts(tmp_path)
	prompt = build_oai_maintain_prompt(
		memory_root=tmp_path / "memory",
		run_folder=run_folder,
		artifact_paths=artifact_paths,
	)
	assert "Cross-Agent Insights" in prompt


# ---------------------------------------------------------------------------
# Schema tests
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
# Ask prompt tests (no LLM calls)
# ---------------------------------------------------------------------------


def test_oai_ask_prompt_contains_question():
	"""Ask prompt should embed the user question."""
	prompt = build_oai_ask_prompt("how to deploy", [], [])
	assert "how to deploy" in prompt


def test_oai_ask_prompt_references_codex():
	"""Ask prompt should reference codex, not explore/grep/glob tools."""
	prompt = build_oai_ask_prompt("test", [], [], memory_root="/tmp/memory")
	assert "codex" in prompt.lower()
	assert "explore()" not in prompt


def test_oai_ask_prompt_with_memory_root():
	"""Ask prompt with memory_root should include search guidance."""
	prompt = build_oai_ask_prompt("test", [], [], memory_root="/tmp/memory")
	assert "Memory root: /tmp/memory" in prompt
	assert "decisions/*.md" in prompt
	assert "learnings/*.md" in prompt


def test_oai_ask_prompt_without_memory_root():
	"""Ask prompt without memory_root should skip search guidance."""
	prompt = build_oai_ask_prompt("test", [], [])
	assert "Memory root" not in prompt


def test_oai_ask_prompt_with_hits():
	"""Ask prompt should include pre-fetched hits."""
	hits = [{"id": "mem-1", "confidence": 0.9, "title": "Deploy tips", "_body": "Use CI."}]
	prompt = build_oai_ask_prompt("deploy?", hits, [])
	assert "mem-1" in prompt
	assert "Deploy tips" in prompt


def test_oai_ask_prompt_with_context_docs():
	"""Ask prompt should include context docs."""
	docs = [{"doc_id": "doc-1", "title": "CI Setup", "body": "Configure pipelines."}]
	prompt = build_oai_ask_prompt("deploy?", [], docs)
	assert "doc-1" in prompt
	assert "CI Setup" in prompt


def test_oai_agent_ask_generates_session_id(tmp_path):
	"""ask() generates a session ID when not provided."""
	cfg = make_config(tmp_path)
	agent = LerimOAIAgent(default_cwd=str(tmp_path), config=cfg)
	sid = agent.generate_session_id()
	assert sid.startswith("lerim-")
	assert len(sid) > 8
