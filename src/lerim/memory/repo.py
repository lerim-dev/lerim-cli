"""Filesystem paths and layout helpers for Lerim memory.

MemoryRepository has been removed. The agent reads/writes memory files directly
via SDK tools. This module keeps standalone path helpers used by settings and CLI.

Two separate directory concerns:
- **Global infrastructure** (~/.lerim): workspace, index, cache, logs — fixed location.
- **Per-project knowledge** (<project>/.lerim): memory files only.

Flat memory directory — all memories live
in memory/ directly. Summaries stay in memory/summaries/. Archived in memory/archived/.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory


@dataclass(frozen=True)
class MemoryPaths:
	"""Resolved canonical paths for one Lerim data root (project or global)."""

	data_dir: Path
	memory_dir: Path


def build_memory_paths(data_dir: Path) -> MemoryPaths:
	"""Build canonical path set rooted at ``data_dir``."""
	data_dir = data_dir.expanduser()
	return MemoryPaths(
		data_dir=data_dir,
		memory_dir=data_dir / "memory",
	)


def ensure_project_memory(paths: MemoryPaths) -> None:
	"""Create required memory folders for a project data root.

	Only creates memory/, summaries/, archived/ — no workspace or index.
	Per-project .lerim/ should contain only knowledge (memory files).
	"""
	for path in (
		paths.memory_dir,
		paths.memory_dir / "summaries",
		paths.memory_dir / "archived",
	):
		path.mkdir(parents=True, exist_ok=True)


def ensure_global_infrastructure(global_data_dir: Path) -> None:
	"""Create required infrastructure folders in the global data root (~/.lerim).

	workspace/, index/, cache/, logs/ belong here — not per-project.
	"""
	global_data_dir = global_data_dir.expanduser()
	for path in (
		global_data_dir / "workspace",
		global_data_dir / "index",
		global_data_dir / "cache",
		global_data_dir / "logs",
	):
		path.mkdir(parents=True, exist_ok=True)


def reset_memory_root(paths: MemoryPaths) -> dict[str, list[str]]:
	"""Delete memory tree for a root and recreate canonical layout.

	Only resets knowledge dirs (memory). Infrastructure dirs (workspace,
	index, cache) are managed globally via ensure_global_infrastructure().
	"""
	removed: list[str] = []
	if paths.memory_dir.exists():
		if paths.memory_dir.is_dir():
			shutil.rmtree(paths.memory_dir, ignore_errors=True)
		else:
			paths.memory_dir.unlink(missing_ok=True)
		removed.append(str(paths.memory_dir))
	ensure_project_memory(paths)
	return {"removed": removed}


def reset_global_infrastructure(global_data_dir: Path) -> dict[str, list[str]]:
	"""Delete infrastructure trees (workspace, index, cache) and recreate."""
	global_data_dir = global_data_dir.expanduser()
	removed: list[str] = []
	for path in (
		global_data_dir / "workspace",
		global_data_dir / "index",
		global_data_dir / "cache",
	):
		if path.exists():
			if path.is_dir():
				shutil.rmtree(path, ignore_errors=True)
			else:
				path.unlink(missing_ok=True)
			removed.append(str(path))
	ensure_global_infrastructure(global_data_dir)
	return {"removed": removed}


if __name__ == "__main__":
	"""Run a real-path smoke test for memory path helpers."""
	with TemporaryDirectory() as tmp_dir:
		root = Path(tmp_dir)
		paths = build_memory_paths(root)

		# Verify path structure
		assert paths.data_dir == root
		assert paths.memory_dir == root / "memory"

		# Ensure creates project memory folders only
		ensure_project_memory(paths)
		assert paths.memory_dir.exists()
		assert (paths.memory_dir / "summaries").exists()
		assert (paths.memory_dir / "archived").exists()
		assert not (root / "workspace").exists()  # NOT created for project
		assert not (root / "index").exists()  # NOT created for project

		# Global infrastructure creates workspace/index
		ensure_global_infrastructure(root)
		assert (root / "workspace").exists()
		assert (root / "index").exists()
		assert (root / "cache").exists()
		assert (root / "logs").exists()

		# Reset memory only
		(paths.memory_dir / "test.md").write_text("test", encoding="utf-8")
		result = reset_memory_root(paths)
		assert str(paths.memory_dir) in result["removed"]
		assert paths.memory_dir.exists()
		assert not (paths.memory_dir / "test.md").exists()

		# Reset infrastructure
		result = reset_global_infrastructure(root)
		assert (root / "workspace").exists()
		assert (root / "index").exists()

	print("memory_repo: self-test passed")
