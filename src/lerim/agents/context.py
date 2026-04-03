"""Runtime context for lerim agent runs.

Frozen dataclass passed to tool functions. Each run gets its own context
with config and artifact locations. Construct ``RuntimeContext`` at the
call site with resolved ``Path`` values (``expanduser().resolve()``) and
a ``Config`` from ``get_config()`` or tests.
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
