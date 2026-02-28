"""Unit tests for memory_repo.py filesystem path helpers.

Tests: build_memory_paths, ensure_memory_paths, reset_memory_root,
and MemoryPaths dataclass field correctness.
"""

from __future__ import annotations

from pathlib import Path

from lerim.memory.memory_repo import (
    build_memory_paths,
    ensure_memory_paths,
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
    assert paths.workspace_dir == tmp_path / "workspace"
    assert paths.index_dir == tmp_path / "index"
    assert paths.memories_db_path == tmp_path / "index" / "memories.sqlite3"
    assert paths.graph_db_path == tmp_path / "index" / "graph.sqlite3"


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
# ensure_memory_paths
# ---------------------------------------------------------------------------


def test_ensure_memory_paths_creates_dirs(tmp_path):
    """ensure_memory_paths creates all canonical subdirectories."""
    paths = build_memory_paths(tmp_path)
    ensure_memory_paths(paths)

    assert (paths.memory_dir / "decisions").is_dir()
    assert (paths.memory_dir / "learnings").is_dir()
    assert (paths.memory_dir / "summaries").is_dir()
    assert (paths.memory_dir / "archived" / "decisions").is_dir()
    assert (paths.memory_dir / "archived" / "learnings").is_dir()
    assert paths.workspace_dir.is_dir()
    assert paths.index_dir.is_dir()


def test_ensure_memory_paths_idempotent(tmp_path):
    """Calling ensure_memory_paths twice does not raise."""
    paths = build_memory_paths(tmp_path)
    ensure_memory_paths(paths)
    ensure_memory_paths(paths)
    assert (paths.memory_dir / "decisions").is_dir()


def test_ensure_memory_paths_preserves_existing_files(tmp_path):
    """ensure_memory_paths does not delete existing files."""
    paths = build_memory_paths(tmp_path)
    ensure_memory_paths(paths)

    test_file = paths.memory_dir / "decisions" / "test.md"
    test_file.write_text("keep me", encoding="utf-8")

    ensure_memory_paths(paths)
    assert test_file.read_text(encoding="utf-8") == "keep me"


# ---------------------------------------------------------------------------
# reset_memory_root
# ---------------------------------------------------------------------------


def test_reset_removes_and_recreates(tmp_path):
    """reset_memory_root clears memory/workspace/index and recreates structure."""
    paths = build_memory_paths(tmp_path)
    ensure_memory_paths(paths)

    # Create a file that should be removed
    sentinel = paths.memory_dir / "learnings" / "20260228-test.md"
    sentinel.write_text("test content", encoding="utf-8")
    assert sentinel.exists()

    result = reset_memory_root(paths)

    # File gone, but directory recreated
    assert not sentinel.exists()
    assert (paths.memory_dir / "learnings").is_dir()
    assert str(paths.memory_dir) in result["removed"]


def test_reset_reports_removed_dirs(tmp_path):
    """reset_memory_root reports which directories were removed."""
    paths = build_memory_paths(tmp_path)
    ensure_memory_paths(paths)

    result = reset_memory_root(paths)
    removed = result["removed"]

    assert str(paths.memory_dir) in removed
    assert str(paths.workspace_dir) in removed
    assert str(paths.index_dir) in removed


def test_reset_on_empty_root(tmp_path):
    """reset_memory_root works even when no directories exist yet."""
    paths = build_memory_paths(tmp_path)
    # Don't call ensure_memory_paths — dirs don't exist
    result = reset_memory_root(paths)

    # Should still create the structure
    assert (paths.memory_dir / "decisions").is_dir()
    assert result["removed"] == []


def test_reset_then_ensure_consistent(tmp_path):
    """After reset, ensure_memory_paths produces same layout."""
    paths = build_memory_paths(tmp_path)
    ensure_memory_paths(paths)

    reset_memory_root(paths)

    # Verify same dirs exist as after a fresh ensure
    assert (paths.memory_dir / "decisions").is_dir()
    assert (paths.memory_dir / "learnings").is_dir()
    assert (paths.memory_dir / "summaries").is_dir()
    assert (paths.memory_dir / "archived" / "decisions").is_dir()
    assert (paths.memory_dir / "archived" / "learnings").is_dir()
    assert paths.workspace_dir.is_dir()
    assert paths.index_dir.is_dir()
