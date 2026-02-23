"""Project/global data directory resolution for Lerim memory scope modes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


def git_root_for(path: Path | None = None) -> Path | None:
    """Return the nearest directory that contains ``.git`` starting from ``path``."""
    start = (path or Path.cwd()).resolve()
    current = start
    while True:
        if (current / ".git").exists():
            return current
        if current.parent == current:
            return None
        current = current.parent


@dataclass(frozen=True)
class ScopeResolution:
    """Resolved project/global data directories and ordered search preference."""

    project_root: Path | None
    project_data_dir: Path | None
    global_data_dir: Path
    ordered_data_dirs: list[Path]


def resolve_data_dirs(
    *,
    scope: str,
    project_dir_name: str,
    global_data_dir: Path,
    repo_path: Path | None = None,
) -> ScopeResolution:
    """Resolve effective memory data roots based on scope mode and repository root."""
    scope = str(scope or "project_fallback_global").strip().lower()
    project_root = git_root_for(repo_path)
    project_data_dir = (project_root / project_dir_name).resolve() if project_root else None
    ordered: list[Path] = []

    if scope == "global_only" or project_data_dir is None:
        ordered = [global_data_dir]
    elif scope == "project_only":
        ordered = [project_data_dir]
    else:
        ordered = [project_data_dir]
        if project_data_dir != global_data_dir:
            ordered.append(global_data_dir)

    seen: set[Path] = set()
    deduped: list[Path] = []
    for path in ordered:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        deduped.append(resolved)

    return ScopeResolution(
        project_root=project_root,
        project_data_dir=project_data_dir,
        global_data_dir=global_data_dir.resolve(),
        ordered_data_dirs=deduped,
    )


if __name__ == "__main__":
    """Run a real-path smoke test for scope resolution logic."""
    cwd = Path.cwd()
    resolved = resolve_data_dirs(
        scope="project_fallback_global",
        project_dir_name=".lerim",
        global_data_dir=Path.home() / ".lerim",
        repo_path=cwd,
    )
    assert resolved.global_data_dir == (Path.home() / ".lerim").resolve()
    assert resolved.ordered_data_dirs
