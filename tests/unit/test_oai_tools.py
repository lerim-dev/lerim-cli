"""Unit tests for OpenAI Agents SDK tools (write_memory, extract/summarize pipelines)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from agents import RunContextWrapper

from lerim.runtime.oai_context import OAIRuntimeContext, build_oai_context
from lerim.runtime.oai_tools import write_memory
from tests.helpers import make_config


def _make_ctx(tmp_path: Path) -> OAIRuntimeContext:
	"""Build test context with memory directories created."""
	memory_root = tmp_path / "memory"
	for sub in ("decisions", "learnings", "summaries"):
		(memory_root / sub).mkdir(parents=True, exist_ok=True)
	run_folder = tmp_path / "workspace" / "run-001"
	run_folder.mkdir(parents=True, exist_ok=True)
	return build_oai_context(
		repo_root=tmp_path,
		memory_root=memory_root,
		workspace_root=tmp_path / "workspace",
		run_folder=run_folder,
		run_id="sync-test-001",
		config=make_config(tmp_path),
	)


def _call_write_memory(ctx: OAIRuntimeContext, **kwargs) -> str:
	"""Call write_memory's underlying logic directly for unit testing.

	The @function_tool decorator wraps the function and its on_invoke_tool
	expects full SDK context (tool_name, call_id, etc.). For unit tests,
	we call the raw function from the module directly.
	"""
	from lerim.runtime import oai_tools as _mod

	# Build a mock wrapper with just .context — the raw function only uses wrapper.context
	class _MockWrapper:
		def __init__(self, context):
			self.context = context

	mock = _MockWrapper(ctx)
	# The decorated function's original code is the module-level function.
	# We can access it via the source module using a non-decorated copy.
	# Simpler: just inline the call since we know the function signature.
	return _mod._write_memory_impl(mock, **kwargs)


# -- write_memory: valid inputs --


def test_write_memory_valid_decision(tmp_path):
	"""Valid decision memory should write a file and return JSON with file_path."""
	ctx = _make_ctx(tmp_path)
	result = _call_write_memory(
		ctx,
		primitive="decision",
		title="Use PostgreSQL",
		body="All persistence should use PostgreSQL.",
		confidence=0.9,
		tags="database,infrastructure",
	)
	parsed = json.loads(result)
	assert parsed["primitive"] == "decision"
	assert Path(parsed["file_path"]).exists()
	content = Path(parsed["file_path"]).read_text()
	assert "Use PostgreSQL" in content
	assert "confidence: 0.9" in content


def test_write_memory_valid_learning(tmp_path):
	"""Valid learning memory with kind should write correctly."""
	ctx = _make_ctx(tmp_path)
	result = _call_write_memory(
		ctx,
		primitive="learning",
		title="Queue heartbeat pattern",
		body="Keep heartbeat updates deterministic.",
		confidence=0.8,
		tags="queue,reliability",
		kind="insight",
	)
	parsed = json.loads(result)
	assert parsed["primitive"] == "learning"
	assert Path(parsed["file_path"]).exists()
	content = Path(parsed["file_path"]).read_text()
	assert "kind: insight" in content


def test_write_memory_tags_parsed(tmp_path):
	"""Comma-separated tags string should be parsed into list."""
	ctx = _make_ctx(tmp_path)
	result = _call_write_memory(
		ctx,
		primitive="decision",
		title="Tag test",
		body="Testing tags.",
		tags="alpha, beta, gamma",
	)
	parsed = json.loads(result)
	content = Path(parsed["file_path"]).read_text()
	assert "alpha" in content
	assert "beta" in content
	assert "gamma" in content


def test_write_memory_default_confidence(tmp_path):
	"""Default confidence should be 0.8."""
	ctx = _make_ctx(tmp_path)
	result = _call_write_memory(
		ctx,
		primitive="decision",
		title="Default confidence test",
		body="Should default to 0.8.",
	)
	parsed = json.loads(result)
	content = Path(parsed["file_path"]).read_text()
	assert "confidence: 0.8" in content


# -- write_memory: validation errors --


def test_write_memory_invalid_primitive(tmp_path):
	"""Invalid primitive should return an ERROR string."""
	ctx = _make_ctx(tmp_path)
	result = _call_write_memory(
		ctx,
		primitive="fact",
		title="Bad",
		body="Bad",
	)
	assert result.startswith("ERROR:")
	assert "decision" in result
	assert "learning" in result


def test_write_memory_learning_missing_kind(tmp_path):
	"""Learning without kind should return an ERROR string."""
	ctx = _make_ctx(tmp_path)
	result = _call_write_memory(
		ctx,
		primitive="learning",
		title="Missing kind",
		body="Should fail.",
	)
	assert result.startswith("ERROR:")
	assert "kind" in result


def test_write_memory_learning_invalid_kind(tmp_path):
	"""Learning with invalid kind should return an ERROR string."""
	ctx = _make_ctx(tmp_path)
	result = _call_write_memory(
		ctx,
		primitive="learning",
		title="Bad kind",
		body="Should fail.",
		kind="tip",
	)
	assert result.startswith("ERROR:")
	assert "kind" in result


def test_write_memory_empty_title(tmp_path):
	"""Empty title should return an ERROR string."""
	ctx = _make_ctx(tmp_path)
	result = _call_write_memory(
		ctx,
		primitive="decision",
		title="",
		body="No title.",
	)
	assert result.startswith("ERROR:")
	assert "title" in result


def test_write_memory_confidence_out_of_range(tmp_path):
	"""Confidence > 1.0 should return an ERROR string."""
	ctx = _make_ctx(tmp_path)
	result = _call_write_memory(
		ctx,
		primitive="decision",
		title="Bad confidence",
		body="Too high.",
		confidence=1.5,
	)
	assert result.startswith("ERROR:")
	assert "confidence" in result


def test_write_memory_confidence_negative(tmp_path):
	"""Negative confidence should return an ERROR string."""
	ctx = _make_ctx(tmp_path)
	result = _call_write_memory(
		ctx,
		primitive="decision",
		title="Negative conf",
		body="Too low.",
		confidence=-0.1,
	)
	assert result.startswith("ERROR:")
	assert "confidence" in result


def test_write_memory_no_memory_root(tmp_path):
	"""Missing memory_root in context should return an ERROR string."""
	ctx = build_oai_context(
		repo_root=tmp_path,
		config=make_config(tmp_path),
	)
	result = _call_write_memory(
		ctx,
		primitive="decision",
		title="No root",
		body="Should fail.",
	)
	assert result.startswith("ERROR:")
	assert "memory_root" in result


# -- Tool schema tests --


def test_write_memory_tool_schema():
	"""write_memory FunctionTool should expose correct parameter schema."""
	schema = write_memory.params_json_schema
	props = schema.get("properties", {})
	assert "primitive" in props
	assert "title" in props
	assert "body" in props
	assert "confidence" in props
	assert "tags" in props
	assert "kind" in props


def test_write_memory_tool_name():
	"""write_memory tool should have the correct name."""
	assert write_memory.name == "write_memory"
