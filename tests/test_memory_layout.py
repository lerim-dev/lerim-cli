"""Tests for canonical memory layout creation and root reset behavior."""

from __future__ import annotations

from lerim.memory.memory_repo import (
    build_memory_paths,
    ensure_memory_paths,
    reset_memory_root,
)


def test_ensure_memory_paths_creates_canonical_folders(tmp_path) -> None:
    layout = build_memory_paths(tmp_path)
    ensure_memory_paths(layout)

    assert (layout.memory_dir / "decisions").exists()
    assert (layout.memory_dir / "learnings").exists()
    assert (layout.memory_dir / "summaries").exists()
    assert layout.workspace_dir.exists()
    assert layout.index_dir.exists()


def test_reset_memory_root_recreates_clean_layout(tmp_path) -> None:
    layout = build_memory_paths(tmp_path)
    ensure_memory_paths(layout)

    learning_path = layout.memory_dir / "learnings" / "example--l20260220abcd.md"
    learning_path.write_text("seed", encoding="utf-8")
    stale_index = layout.index_dir / "fts.sqlite3"
    stale_index.write_text("", encoding="utf-8")

    result = reset_memory_root(layout)

    removed = set(result["removed"])
    assert str(layout.memory_dir) in removed
    assert str(layout.index_dir) in removed
    assert (layout.memory_dir / "learnings").exists()
    assert not learning_path.exists()
    assert not stale_index.exists()
