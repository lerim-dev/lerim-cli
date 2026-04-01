"""Runtime context for lerim agent runs.

Frozen dataclass passed to tool functions. Each run gets its own context
with resolved paths, config, and artifact locations.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from lerim.config.settings import Config


@dataclass(frozen=True)
class RuntimeContext:
	"""Per-run context passed to tool functions."""

	config: Config
	repo_root: Path
	memory_root: Path | None
	workspace_root: Path | None
	run_folder: Path | None
	extra_read_roots: tuple[Path, ...]
	run_id: str
	trace_path: Path | None = None
	artifact_paths: dict[str, Path] | None = None


def build_context(
	*,
	repo_root: str | Path,
	memory_root: str | Path | None = None,
	workspace_root: str | Path | None = None,
	run_folder: str | Path | None = None,
	extra_read_roots: tuple[str | Path, ...] | None = None,
	run_id: str = "",
	config: Config | None = None,
	trace_path: str | Path | None = None,
	artifact_paths: dict[str, Path] | None = None,
) -> RuntimeContext:
	"""Build canonical runtime context for one agent run."""
	from lerim.config.settings import get_config
	cfg = config or get_config()
	return RuntimeContext(
		config=cfg,
		repo_root=Path(repo_root).expanduser().resolve(),
		memory_root=Path(memory_root).expanduser().resolve() if memory_root else None,
		workspace_root=Path(workspace_root).expanduser().resolve() if workspace_root else None,
		run_folder=Path(run_folder).expanduser().resolve() if run_folder else None,
		extra_read_roots=tuple(
			Path(p).expanduser().resolve() for p in (extra_read_roots or [])
		),
		run_id=str(run_id or ""),
		trace_path=Path(trace_path).expanduser().resolve() if trace_path else None,
		artifact_paths=artifact_paths,
	)
