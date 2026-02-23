"""Filesystem paths and layout helpers for Lerim memory.

MemoryRepository has been removed. The agent reads/writes memory files directly
via SDK tools. This module keeps standalone path helpers used by settings and CLI.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from lerim.memory.memory_record import MemoryType, memory_folder


@dataclass(frozen=True)
class MemoryPaths:
    """Resolved canonical paths for one Lerim data root."""

    data_dir: Path
    memory_dir: Path
    workspace_dir: Path
    index_dir: Path
    memories_db_path: Path
    graph_db_path: Path


def build_memory_paths(data_dir: Path) -> MemoryPaths:
    """Build canonical path set rooted at ``data_dir``."""
    data_dir = data_dir.expanduser()
    index_dir = data_dir / "index"
    return MemoryPaths(
        data_dir=data_dir,
        memory_dir=data_dir / "memory",
        workspace_dir=data_dir / "workspace",
        index_dir=index_dir,
        memories_db_path=index_dir / "memories.sqlite3",
        graph_db_path=index_dir / "graph.sqlite3",
    )


def ensure_memory_paths(paths: MemoryPaths) -> None:
    """Create required canonical memory folders when missing."""
    archive_types = (MemoryType.decision, MemoryType.learning)
    memory_paths = tuple(paths.memory_dir / memory_folder(item) for item in MemoryType)
    archive_paths = tuple(
        paths.memory_dir / "archived" / memory_folder(item) for item in archive_types
    )
    for path in (
        *memory_paths,
        *archive_paths,
        paths.workspace_dir,
        paths.index_dir,
    ):
        path.mkdir(parents=True, exist_ok=True)


def reset_memory_root(paths: MemoryPaths) -> dict[str, list[str]]:
    """Delete memory/index/workspace trees for a root and recreate canonical layout."""
    removed: list[str] = []
    for path in (paths.memory_dir, paths.workspace_dir, paths.index_dir):
        if path.exists():
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)
            removed.append(str(path))
    ensure_memory_paths(paths)
    return {"removed": removed}


if __name__ == "__main__":
    """Run a real-path smoke test for memory path helpers."""
    with TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        paths = build_memory_paths(root)

        # Verify path structure
        assert paths.data_dir == root
        assert paths.memory_dir == root / "memory"
        assert paths.workspace_dir == root / "workspace"
        assert paths.index_dir == root / "index"

        # Ensure creates folders
        ensure_memory_paths(paths)
        assert (paths.memory_dir / "decisions").exists()
        assert (paths.memory_dir / "learnings").exists()
        assert (paths.memory_dir / "summaries").exists()
        assert (paths.memory_dir / "archived" / "decisions").exists()
        assert (paths.memory_dir / "archived" / "learnings").exists()
        assert paths.workspace_dir.exists()
        assert paths.index_dir.exists()

        # Reset removes and recreates
        (paths.memory_dir / "learnings" / "test.md").write_text(
            "test", encoding="utf-8"
        )
        result = reset_memory_root(paths)
        assert str(paths.memory_dir) in result["removed"]
        assert (paths.memory_dir / "learnings").exists()
        assert not (paths.memory_dir / "learnings" / "test.md").exists()
