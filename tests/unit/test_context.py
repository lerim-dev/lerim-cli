"""Unit tests for RuntimeContext."""

from __future__ import annotations

from pathlib import Path

import pytest

from lerim.agents.context import RuntimeContext
from tests.helpers import make_config


def test_runtime_context_paths_absolute(tmp_path):
	"""Path fields can be stored as resolved absolute paths."""
	cfg = make_config(tmp_path)
	repo = Path(tmp_path).resolve()
	mem = (tmp_path / "memory").resolve()
	ws = (tmp_path / "workspace").resolve()
	run = (tmp_path / "workspace" / "run-001").resolve()
	ctx = RuntimeContext(
		config=cfg,
		repo_root=repo,
		memory_root=mem,
		workspace_root=ws,
		run_folder=run,
		extra_read_roots=(),
		run_id="test-run",
	)
	assert ctx.repo_root.is_absolute()
	assert ctx.memory_root.is_absolute()
	assert ctx.workspace_root.is_absolute()
	assert ctx.run_folder.is_absolute()
	assert ctx.run_id == "test-run"


def test_runtime_context_none_optionals(tmp_path):
	"""Optional fields may be None."""
	cfg = make_config(tmp_path)
	repo = Path(tmp_path).resolve()
	ctx = RuntimeContext(
		config=cfg,
		repo_root=repo,
		memory_root=None,
		workspace_root=None,
		run_folder=None,
		extra_read_roots=(),
		run_id="",
	)
	assert ctx.trace_path is None
	assert ctx.artifact_paths is None


def test_context_is_frozen(tmp_path):
	"""Context should be immutable (frozen dataclass)."""
	cfg = make_config(tmp_path)
	repo = Path(tmp_path).resolve()
	ctx = RuntimeContext(
		config=cfg,
		repo_root=repo,
		memory_root=None,
		workspace_root=None,
		run_folder=None,
		extra_read_roots=(),
		run_id="",
	)
	with pytest.raises(AttributeError):
		ctx.run_id = "changed"


def test_runtime_context_trace_and_artifacts(tmp_path):
	"""trace_path and artifact_paths are stored when provided."""
	cfg = make_config(tmp_path)
	repo = Path(tmp_path).resolve()
	trace = (tmp_path / "trace.jsonl").resolve()
	artifacts = {"summary": tmp_path / "summary.json"}
	ctx = RuntimeContext(
		config=cfg,
		repo_root=repo,
		memory_root=None,
		workspace_root=None,
		run_folder=None,
		extra_read_roots=(),
		run_id="",
		trace_path=trace,
		artifact_paths=artifacts,
	)
	assert ctx.trace_path == trace
	assert ctx.artifact_paths == artifacts


def test_runtime_context_extra_read_roots(tmp_path):
	"""extra_read_roots is a tuple of paths."""
	cfg = make_config(tmp_path)
	repo = Path(tmp_path).resolve()
	extra = (tmp_path / "extra").resolve()
	ctx = RuntimeContext(
		config=cfg,
		repo_root=repo,
		memory_root=None,
		workspace_root=None,
		run_folder=None,
		extra_read_roots=(extra,),
		run_id="",
	)
	assert len(ctx.extra_read_roots) == 1
	assert ctx.extra_read_roots[0].is_absolute()
