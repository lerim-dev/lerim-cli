"""Unit tests for OpenAI Agents SDK runtime context."""

from __future__ import annotations


from lerim.runtime.oai_context import build_oai_context
from tests.helpers import make_config


def test_build_oai_context_resolves_paths(tmp_path):
	"""All path fields should be resolved to absolute paths."""
	ctx = build_oai_context(
		repo_root=tmp_path,
		memory_root=tmp_path / "memory",
		workspace_root=tmp_path / "workspace",
		run_folder=tmp_path / "workspace" / "run-001",
		run_id="test-run",
		config=make_config(tmp_path),
	)
	assert ctx.repo_root.is_absolute()
	assert ctx.memory_root.is_absolute()
	assert ctx.workspace_root.is_absolute()
	assert ctx.run_folder.is_absolute()
	assert ctx.run_id == "test-run"


def test_build_oai_context_none_optionals(tmp_path):
	"""Optional fields default to None when not provided."""
	ctx = build_oai_context(
		repo_root=tmp_path,
		config=make_config(tmp_path),
	)
	assert ctx.memory_root is None
	assert ctx.workspace_root is None
	assert ctx.run_folder is None
	assert ctx.trace_path is None
	assert ctx.artifact_paths is None
	assert ctx.extra_read_roots == ()


def test_oai_context_is_frozen(tmp_path):
	"""Context should be immutable (frozen dataclass)."""
	ctx = build_oai_context(
		repo_root=tmp_path,
		config=make_config(tmp_path),
	)
	import pytest
	with pytest.raises(AttributeError):
		ctx.run_id = "changed"


def test_build_oai_context_with_trace_and_artifacts(tmp_path):
	"""trace_path and artifact_paths should be set when provided."""
	trace = tmp_path / "trace.jsonl"
	artifacts = {"extract": tmp_path / "extract.json"}
	ctx = build_oai_context(
		repo_root=tmp_path,
		trace_path=trace,
		artifact_paths=artifacts,
		config=make_config(tmp_path),
	)
	assert ctx.trace_path == trace.resolve()
	assert ctx.artifact_paths == artifacts


def test_build_oai_context_extra_read_roots(tmp_path):
	"""extra_read_roots should be resolved and stored as tuple."""
	extra = tmp_path / "extra"
	ctx = build_oai_context(
		repo_root=tmp_path,
		extra_read_roots=(extra,),
		config=make_config(tmp_path),
	)
	assert len(ctx.extra_read_roots) == 1
	assert ctx.extra_read_roots[0].is_absolute()
