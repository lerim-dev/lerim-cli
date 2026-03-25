"""Unit tests for LerimOAIAgent sync flow."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from lerim.config.settings import LLMRoleConfig
from lerim.runtime.oai_agent import LerimOAIAgent
from lerim.runtime.prompts.oai_sync import build_oai_sync_prompt
from tests.helpers import make_config


# ---------------------------------------------------------------------------
# Prompt builder tests (no LLM calls)
# ---------------------------------------------------------------------------


def test_oai_sync_prompt_contains_steps(tmp_path):
	"""Sync prompt should contain all 6 steps."""
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
	assert "SCAN EXISTING MEMORIES" in prompt
	assert "EXTRACT + SUMMARIZE" in prompt
	assert "READ EXTRACT RESULTS" in prompt
	assert "DEDUPE CANDIDATES" in prompt
	assert "WRITE MEMORIES" in prompt
	assert "WRITE REPORT" in prompt


def test_oai_sync_prompt_references_codex(tmp_path):
	"""Sync prompt should reference codex, not explore/read/write tools."""
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
	assert "codex" in prompt.lower()
	assert "write_memory" in prompt
	assert "extract_pipeline" in prompt
	assert "summarize_pipeline" in prompt
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
	assert "72%" in prompt


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
	assert agent._needs_proxy is True
	assert agent._proxy is not None


def test_oai_agent_init_openai_no_proxy(tmp_path):
	"""OpenAI provider should not need a proxy."""
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
	cfg = replace(cfg, lead_role=openai_role, openai_api_key="test-key")
	agent = LerimOAIAgent(default_cwd=str(tmp_path), config=cfg)
	assert agent._needs_proxy is False
	assert agent._proxy is None


def test_oai_agent_sync_missing_trace(tmp_path):
	"""sync() should raise FileNotFoundError for missing trace."""
	cfg = make_config(tmp_path)
	cfg = replace(cfg, minimax_api_key="test-key")
	agent = LerimOAIAgent(default_cwd=str(tmp_path), config=cfg)
	with pytest.raises(FileNotFoundError, match="trace_path_missing"):
		agent.sync(tmp_path / "nonexistent.jsonl")
