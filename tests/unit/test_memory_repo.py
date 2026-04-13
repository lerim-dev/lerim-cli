"""Unit tests for memory_repo.py filesystem path helpers.

Tests: build_memory_paths, ensure_project_memory, ensure_global_infrastructure,
reset_memory_root, reset_global_infrastructure, and MemoryPaths dataclass field correctness.
"""

from __future__ import annotations

from pathlib import Path

from lerim.memory.repo import (
	build_memory_paths,
	ensure_global_infrastructure,
	ensure_project_memory,
	reset_global_infrastructure,
	reset_memory_root,
)


# ---------------------------------------------------------------------------
# build_memory_paths
# ---------------------------------------------------------------------------


def test_build_memory_paths_structure(tmp_path):
	"""build_memory_paths returns correct canonical path set."""
	paths = build_memory_paths(tmp_path)
	assert paths.data_dir == tmp_path
	assert paths.memory_dir == tmp_path / "memory"


def test_build_memory_paths_expands_user():
	"""build_memory_paths expands ~ in the data_dir path."""
	paths = build_memory_paths(Path("~/test-lerim"))
	assert "~" not in str(paths.data_dir)
	assert paths.data_dir == Path.home() / "test-lerim"


def test_memory_paths_is_frozen(tmp_path):
	"""MemoryPaths dataclass is immutable."""
	paths = build_memory_paths(tmp_path)
	try:
		paths.data_dir = tmp_path / "other"
		assert False, "Should have raised FrozenInstanceError"
	except AttributeError:
		pass


# ---------------------------------------------------------------------------
# ensure_project_memory
# ---------------------------------------------------------------------------


def test_ensure_project_memory_creates_dirs(tmp_path):
	"""ensure_project_memory creates memory subdirectories only."""
	paths = build_memory_paths(tmp_path)
	ensure_project_memory(paths)

	assert paths.memory_dir.is_dir()
	assert (paths.memory_dir / "summaries").is_dir()
	assert (paths.memory_dir / "archived").is_dir()
	# workspace and index are NOT created by ensure_project_memory
	assert not (tmp_path / "workspace").exists()
	assert not (tmp_path / "index").exists()


def test_ensure_project_memory_idempotent(tmp_path):
	"""Calling ensure_project_memory twice does not raise."""
	paths = build_memory_paths(tmp_path)
	ensure_project_memory(paths)
	ensure_project_memory(paths)
	assert paths.memory_dir.is_dir()


def test_ensure_project_memory_preserves_existing_files(tmp_path):
	"""ensure_project_memory does not delete existing files."""
	paths = build_memory_paths(tmp_path)
	ensure_project_memory(paths)

	test_file = paths.memory_dir / "test.md"
	test_file.write_text("keep me", encoding="utf-8")

	ensure_project_memory(paths)
	assert test_file.read_text(encoding="utf-8") == "keep me"


# ---------------------------------------------------------------------------
# reset_memory_root
# ---------------------------------------------------------------------------


def test_reset_removes_and_recreates(tmp_path):
	"""reset_memory_root clears memory and recreates structure."""
	paths = build_memory_paths(tmp_path)
	ensure_project_memory(paths)

	# Create a file that should be removed
	sentinel = paths.memory_dir / "20260228-test.md"
	sentinel.write_text("test content", encoding="utf-8")
	assert sentinel.exists()

	result = reset_memory_root(paths)

	# File gone, but directory recreated
	assert not sentinel.exists()
	assert paths.memory_dir.is_dir()
	assert str(paths.memory_dir) in result["removed"]


def test_reset_reports_removed_dirs(tmp_path):
	"""reset_memory_root reports which directories were removed."""
	paths = build_memory_paths(tmp_path)
	ensure_project_memory(paths)

	result = reset_memory_root(paths)
	removed = result["removed"]

	assert str(paths.memory_dir) in removed


def test_reset_on_empty_root(tmp_path):
	"""reset_memory_root works even when no directories exist yet."""
	paths = build_memory_paths(tmp_path)
	# Don't call ensure_project_memory -- dirs don't exist
	result = reset_memory_root(paths)

	# Should still create the structure
	assert paths.memory_dir.is_dir()
	assert result["removed"] == []


def test_reset_then_ensure_consistent(tmp_path):
	"""After reset, ensure_project_memory produces same layout."""
	paths = build_memory_paths(tmp_path)
	ensure_project_memory(paths)

	reset_memory_root(paths)

	# Verify same dirs exist as after a fresh ensure
	assert paths.memory_dir.is_dir()
	assert (paths.memory_dir / "summaries").is_dir()
	assert (paths.memory_dir / "archived").is_dir()


def test_reset_handles_file_instead_of_dir(tmp_path):
	"""reset_memory_root removes a plain file if it exists where a dir is expected."""
	paths = build_memory_paths(tmp_path)

	# Place a file where memory_dir would be (not a directory)
	paths.memory_dir.parent.mkdir(parents=True, exist_ok=True)
	paths.memory_dir.write_text("I am a file, not a dir", encoding="utf-8")
	assert paths.memory_dir.is_file()

	result = reset_memory_root(paths)

	# File should have been unlinked, and the dir recreated
	assert paths.memory_dir.is_dir()
	assert str(paths.memory_dir) in result["removed"]


def test_reset_creates_summaries_and_archived(tmp_path):
	"""reset_memory_root recreates summaries and archived subdirs after clearing."""
	paths = build_memory_paths(tmp_path)
	ensure_project_memory(paths)

	# Put content in summaries
	summary_file = paths.memory_dir / "summaries" / "test.md"
	summary_file.write_text("summary", encoding="utf-8")

	reset_memory_root(paths)

	assert (paths.memory_dir / "summaries").is_dir()
	assert (paths.memory_dir / "archived").is_dir()
	assert not summary_file.exists()


# ---------------------------------------------------------------------------
# ensure_global_infrastructure / reset_global_infrastructure
# ---------------------------------------------------------------------------


def test_ensure_global_infrastructure_creates_dirs(tmp_path):
	"""ensure_global_infrastructure creates workspace, index, cache, and logs."""
	ensure_global_infrastructure(tmp_path)

	assert (tmp_path / "workspace").is_dir()
	assert (tmp_path / "index").is_dir()
	assert (tmp_path / "cache").is_dir()
	assert (tmp_path / "logs").is_dir()


def test_reset_global_infrastructure(tmp_path):
	"""reset_global_infrastructure removes workspace/index/cache and recreates."""
	ensure_global_infrastructure(tmp_path)

	# Place sentinel files
	sentinel = tmp_path / "index" / "fts.sqlite3"
	sentinel.write_text("stale", encoding="utf-8")
	assert sentinel.exists()

	result = reset_global_infrastructure(tmp_path)

	# Sentinel gone, but dirs recreated
	assert not sentinel.exists()
	assert (tmp_path / "workspace").is_dir()
	assert (tmp_path / "index").is_dir()
	assert (tmp_path / "cache").is_dir()
	assert (tmp_path / "logs").is_dir()
	assert str(tmp_path / "workspace") in result["removed"]
	assert str(tmp_path / "index") in result["removed"]
	assert str(tmp_path / "cache") in result["removed"]
