"""Shared utilities for sync and maintain runtime flows.

These helpers handle artifact I/O, path resolution, counter extraction,
and the stable result contracts consumed by the CLI, daemon, and shipper.
They are intentionally free of any PydanticAI or provider-specific imports
so that both the PydanticAI and OpenAI-Agents backends can reuse them.
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from lerim.config.settings import Config
from lerim.runtime.contracts import MaintainCounts, SyncCounts


# ---------------------------------------------------------------------------
# Stable result contracts
# ---------------------------------------------------------------------------

class SyncResultContract(BaseModel):
	"""Stable sync return payload schema used by CLI and daemon."""

	trace_path: str
	memory_root: str
	workspace_root: str
	run_folder: str
	artifacts: dict[str, str]
	counts: SyncCounts
	written_memory_paths: list[str]
	summary_path: str
	cost_usd: float = 0.0


class MaintainResultContract(BaseModel):
	"""Stable maintain return payload schema used by CLI and daemon."""

	memory_root: str
	workspace_root: str
	run_folder: str
	artifacts: dict[str, str]
	counts: MaintainCounts
	cost_usd: float = 0.0


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _default_run_folder_name(prefix: str = "sync") -> str:
	"""Build deterministic per-run workspace folder name with given prefix."""
	stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
	return f"{prefix}-{stamp}-{secrets.token_hex(3)}"


def _build_artifact_paths(run_folder: Path) -> dict[str, Path]:
	"""Return canonical workspace artifact paths for a sync run folder."""
	return {
		"extract": run_folder / "extract.json",
		"summary": run_folder / "summary.json",
		"memory_actions": run_folder / "memory_actions.json",
		"agent_log": run_folder / "agent.log",
		"subagents_log": run_folder / "subagents.log",
		"session_log": run_folder / "session.log",
	}


def _resolve_runtime_roots(
	*,
	config: Config,
	memory_root: str | Path | None,
	workspace_root: str | Path | None,
) -> tuple[Path, Path]:
	"""Resolve memory/workspace roots using config defaults when unset."""
	resolved_memory_root = (
		Path(memory_root).expanduser().resolve() if memory_root else config.memory_dir
	)
	resolved_workspace_root = (
		Path(workspace_root).expanduser().resolve()
		if workspace_root
		else (config.data_dir / "workspace")
	)
	return resolved_memory_root, resolved_workspace_root


# ---------------------------------------------------------------------------
# Artifact I/O
# ---------------------------------------------------------------------------

def _write_json_artifact(path: Path, payload: dict[str, Any]) -> None:
	"""Write artifact payload as UTF-8 JSON with trailing newline."""
	path.write_text(
		json.dumps(payload, ensure_ascii=True, indent=2) + "\n", encoding="utf-8"
	)


def _load_json_dict_artifact(path: Path) -> dict[str, Any]:
	"""Read a JSON artifact and enforce top-level object type."""
	try:
		data = json.loads(path.read_text(encoding="utf-8"))
	except json.JSONDecodeError as exc:
		raise RuntimeError(f"invalid_json_artifact:{path}") from exc
	if not isinstance(data, dict):
		raise RuntimeError(f"invalid_report_shape:{path}")
	return data


def _extract_counts(
	counts_raw: dict[str, Any],
	fields: dict[str, tuple[str, ...]],
) -> dict[str, int]:
	"""Extract integer counters from a raw report map using aliases."""
	counts: dict[str, int] = {}
	for output_key, aliases in fields.items():
		value = 0
		for alias in aliases:
			candidate = counts_raw.get(alias)
			if candidate is not None:
				value = int(candidate or 0)
				break
		counts[output_key] = value
	return counts


def _write_text_with_newline(path: Path, content: str) -> None:
	"""Write text artifact ensuring exactly one trailing newline."""
	text = content if content.endswith("\n") else f"{content}\n"
	path.write_text(text, encoding="utf-8")
