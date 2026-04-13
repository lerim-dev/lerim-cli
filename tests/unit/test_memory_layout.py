"""Tests for canonical memory layout creation and root reset behavior."""

from __future__ import annotations

from lerim.memory.repo import (
	build_memory_paths,
	ensure_global_infrastructure,
	ensure_project_memory,
	reset_memory_root,
)


def test_ensure_project_memory_creates_canonical_folders(tmp_path) -> None:
	layout = build_memory_paths(tmp_path)
	ensure_project_memory(layout)

	assert layout.memory_dir.exists()
	assert (layout.memory_dir / "summaries").exists()
	assert (layout.memory_dir / "archived").exists()
	# workspace and index are global infrastructure, not per-project
	assert not (tmp_path / "workspace").exists()
	assert not (tmp_path / "index").exists()


def test_ensure_global_infrastructure_creates_dirs(tmp_path) -> None:
	ensure_global_infrastructure(tmp_path)

	assert (tmp_path / "workspace").exists()
	assert (tmp_path / "index").exists()
	assert (tmp_path / "cache").exists()
	assert (tmp_path / "logs").exists()


def test_reset_memory_root_recreates_clean_layout(tmp_path) -> None:
	layout = build_memory_paths(tmp_path)
	ensure_project_memory(layout)

	memory_file = layout.memory_dir / "example--l20260220abcd.md"
	memory_file.write_text("seed", encoding="utf-8")

	result = reset_memory_root(layout)

	removed = set(result["removed"])
	assert str(layout.memory_dir) in removed
	assert layout.memory_dir.exists()
	assert not memory_file.exists()
