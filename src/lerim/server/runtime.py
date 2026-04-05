"""DSPy ReAct runtime for Lerim sync, maintain, and ask flows.

Synchronous DSPy ReAct orchestrator. Creates DSPy modules (ExtractAgent,
MaintainAgent, AskAgent) per call and runs them via dspy.context(lm=...)
for thread-safe model switching.
"""

from __future__ import annotations

import json
import logging
import secrets
import time
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import dspy
from lerim.config.settings import Config, get_config
from lerim.agents.ask import AskAgent
from lerim.agents.contracts import (
	MaintainResultContract,
	SyncResultContract,
)
from lerim.agents.ask import format_ask_hints
from lerim.agents.maintain import MaintainAgent
from lerim.config.providers import build_dspy_fallback_lms, build_dspy_lm
from lerim.agents.extract import ExtractAgent

logger = logging.getLogger("lerim.runtime")


# ---------------------------------------------------------------------------
# Path helpers (inlined from helpers.py)
# ---------------------------------------------------------------------------

def _default_run_folder_name(prefix: str = "sync") -> str:
	"""Build deterministic per-run workspace folder name with given prefix."""
	stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
	return f"{prefix}-{stamp}-{secrets.token_hex(3)}"


def build_maintain_artifact_paths(run_folder: Path) -> dict[str, Path]:
	"""Return canonical workspace artifact paths for a maintain run folder."""
	return {
		"agent_log": run_folder / "agent.log",
		"subagents_log": run_folder / "subagents.log",
	}


def _build_artifact_paths(run_folder: Path) -> dict[str, Path]:
	"""Return canonical workspace artifact paths for a sync run folder."""
	return {
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
# Artifact I/O (inlined from helpers.py)
# ---------------------------------------------------------------------------

def _write_json_artifact(path: Path, payload: dict[str, Any]) -> None:
	"""Write artifact payload as UTF-8 JSON with trailing newline."""
	path.write_text(
		json.dumps(payload, ensure_ascii=True, indent=2) + "\n", encoding="utf-8"
	)


def _write_text_with_newline(path: Path, content: str) -> None:
	"""Write text artifact ensuring exactly one trailing newline."""
	text = content if content.endswith("\n") else f"{content}\n"
	path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Cost tracking (inlined from cost_tracker.py)
# ---------------------------------------------------------------------------

class _Acc:
	"""Mutable cost accumulator shared by reference across context copies."""

	__slots__ = ("total",)

	def __init__(self) -> None:
		"""Initialize accumulator with zero total."""
		self.total = 0.0


_run_cost: ContextVar[_Acc | None] = ContextVar("lerim_run_cost", default=None)


def start_cost_tracking() -> None:
	"""Begin accumulating LLM cost for the current run."""
	_run_cost.set(_Acc())


def stop_cost_tracking() -> float:
	"""Stop tracking and return accumulated cost in USD."""
	acc = _run_cost.get(None)
	cost = acc.total if acc else 0.0
	_run_cost.set(None)
	return cost


def add_cost(amount: float) -> None:
	"""Add cost to the current run's accumulator (no-op when tracking inactive)."""
	acc = _run_cost.get(None)
	if acc is not None:
		acc.total += amount


def capture_dspy_cost(lm: object, history_start: int) -> None:
	"""Add cost from DSPy LM history entries added since *history_start*."""
	history = getattr(lm, "history", None)
	if not isinstance(history, list):
		return
	for entry in history[history_start:]:
		if not isinstance(entry, dict):
			continue
		response = entry.get("response")
		if response is None:
			continue
		usage = getattr(response, "usage", None)
		if usage is None:
			continue
		cost = getattr(usage, "cost", None)
		if cost is None and isinstance(usage, dict):
			cost = usage.get("cost")
		if cost is not None:
			add_cost(float(cost))


# ---------------------------------------------------------------------------
# Trajectory adapter: convert ReAct trajectory dict to trace list
# ---------------------------------------------------------------------------

def _trajectory_to_trace_list(trajectory: dict) -> list[dict]:
	"""Convert a dspy.ReAct trajectory dict to a serializable trace list.

	ReAct stores its trace as thought_0, tool_name_0, tool_args_0,
	observation_0, thought_1, ... This converts that into a list of
	message-style dicts for agent_trace.json.
	"""
	trace: list[dict] = []
	idx = 0
	while f"thought_{idx}" in trajectory:
		trace.append({
			"role": "assistant",
			"content": trajectory[f"thought_{idx}"],
		})
		trace.append({
			"role": "assistant",
			"tool_call": {
				"name": trajectory.get(f"tool_name_{idx}"),
				"arguments": trajectory.get(f"tool_args_{idx}", {}),
			},
		})
		trace.append({
			"role": "tool",
			"name": trajectory.get(f"tool_name_{idx}"),
			"content": str(trajectory.get(f"observation_{idx}", "")),
		})
		idx += 1
	return trace


class LerimRuntime:
	"""Runtime orchestrator for DSPy ReAct sync, maintain, and ask flows."""

	def __init__(
		self,
		default_cwd: str | None = None,
		config: Config | None = None,
	) -> None:
		"""Create DSPy ReAct runtime with model configuration.

		Args:
			default_cwd: Default working directory for path resolution.
			config: Lerim config; loaded via get_config() if not provided.
		"""
		cfg = config or get_config()
		self.config = cfg
		self._default_cwd = default_cwd

		# Validate agent provider.
		from lerim.config.providers import validate_provider_for_role
		validate_provider_for_role(cfg.agent_role.provider, "agent")

		# Build agent LM via DSPy provider builder.
		self._lead_lm: dspy.LM = build_dspy_lm("agent", config=cfg)

		# Build fallback LMs from config (e.g. fallback_models = ["minimax:minimax-m2.5"]).
		self._fallback_lms: list[dspy.LM] = build_dspy_fallback_lms(
			"agent", config=cfg,
		)

	# ------------------------------------------------------------------
	# Shared helpers
	# ------------------------------------------------------------------


	@staticmethod
	def _is_quota_error(error_msg: str) -> bool:
		"""Return True if the error message indicates a quota/rate-limit error."""
		lower = error_msg.lower()
		return "429" in error_msg or "rate limit" in lower or "quota" in lower

	@staticmethod
	def generate_session_id() -> str:
		"""Generate a unique session ID for ask mode."""
		import secrets
		return f"lerim-{secrets.token_hex(6)}"

	# ------------------------------------------------------------------
	# Retry + fallback
	# ------------------------------------------------------------------

	def _run_with_fallback(
		self,
		*,
		flow: str,
		module: dspy.Module,
		input_args: dict[str, Any],
		max_attempts: int = 3,
	) -> dspy.Prediction:
		"""Run a DSPy module with retry + fallback model support.

		Tries the primary LM first with up to max_attempts retries.
		On quota/rate-limit errors, switches to the next fallback LM.
		Non-quota errors retry the same LM with exponential backoff.

		Args:
			flow: Flow name for log messages (e.g. "sync", "maintain", "ask").
			module: The DSPy module to call (ExtractAgent, MaintainAgent, AskAgent).
			input_args: Keyword arguments passed to module(**input_args).
			max_attempts: Retry attempts per model.

		Returns:
			The dspy.Prediction result.

		Raises:
			RuntimeError: If all models and attempts are exhausted.
		"""
		lms = [self._lead_lm] + self._fallback_lms
		last_error: Exception | None = None

		for model_idx, lm in enumerate(lms):
			model_label = (
				self.config.agent_role.model
				if model_idx == 0
				else f"fallback-{model_idx}"
			)
			for attempt in range(1, max_attempts + 1):
				try:
					logger.info(
						f"[{flow}] ReAct attempt {attempt}/{max_attempts} "
						f"(model={model_label})"
					)
					with dspy.context(lm=lm, adapter=dspy.XMLAdapter()):
						return module(**input_args)
				except Exception as exc:
					last_error = exc
					error_msg = str(exc)

					if self._is_quota_error(error_msg):
						logger.warning(
							f"[{flow}] Quota/rate-limit on attempt "
							f"{attempt}: {error_msg[:100]}"
						)
						if model_idx < len(lms) - 1:
							fb_label = (
								self.config.agent_role.fallback_models[model_idx]
								if model_idx < len(self.config.agent_role.fallback_models)
								else f"fallback-{model_idx + 1}"
							)
							logger.warning(
								f"[{flow}] Switching to fallback: {fb_label}"
							)
							break  # Break inner retry loop, try next model
						# Last model -- keep retrying with backoff
					elif "500" in error_msg or "503" in error_msg:
						logger.warning(
							f"[{flow}] Server error on attempt "
							f"{attempt}: {error_msg[:100]}"
						)
					elif attempt < max_attempts:
						logger.warning(
							f"[{flow}] Error on attempt {attempt} "
							f"({type(exc).__name__}): {error_msg[:100]}"
						)

					if attempt < max_attempts:
						wait_time = min(2 ** attempt, 8)
						logger.info(f"[{flow}] Retrying in {wait_time}s...")
						time.sleep(wait_time)
			else:
				# Inner loop exhausted all attempts without breaking
				continue
			# Inner loop broke (quota error, switching to next model)
			continue

		raise RuntimeError(
			f"[{flow}] Failed after trying {len(lms)} model(s). "
			f"Last error: {last_error}"
		) from last_error

	# ------------------------------------------------------------------
	# Sync flow
	# ------------------------------------------------------------------

	def sync(
		self,
		trace_path: str | Path,
		memory_root: str | Path | None = None,
		workspace_root: str | Path | None = None,
	) -> dict[str, Any]:
		"""Run memory-write sync flow and return stable contract payload.

		Run memory-write sync and return a SyncResultContract-validated dict.

		Args:
			trace_path: Path to the session trace JSONL file.
			memory_root: Override for the memory directory.
			workspace_root: Override for the workspace directory.

		Returns:
			Validated SyncResultContract payload dict.

		Raises:
			FileNotFoundError: If trace_path does not exist.
			RuntimeError: On agent failure or missing artifacts.
		"""
		trace_file = Path(trace_path).expanduser().resolve()
		if not trace_file.exists() or not trace_file.is_file():
			raise FileNotFoundError(f"trace_path_missing:{trace_file}")

		repo_root = Path(self._default_cwd or Path.cwd()).expanduser().resolve()
		return self._sync_inner(trace_file, repo_root, memory_root, workspace_root)

	def _sync_inner(
		self,
		trace_file: Path,
		repo_root: Path,
		memory_root: str | Path | None,
		workspace_root: str | Path | None,
	) -> dict[str, Any]:
		"""Inner sync logic called by sync()."""
		resolved_memory_root, resolved_workspace_root = _resolve_runtime_roots(
			config=self.config,
			memory_root=memory_root,
			workspace_root=workspace_root,
		)
		run_folder = resolved_workspace_root / _default_run_folder_name("sync")
		run_folder.mkdir(parents=True, exist_ok=True)
		artifact_paths = _build_artifact_paths(run_folder)

		metadata = {
			"run_id": run_folder.name,
			"trace_path": str(trace_file),
			"repo_name": repo_root.name,
		}
		_write_json_artifact(artifact_paths["session_log"], metadata)
		artifact_paths["subagents_log"].write_text("", encoding="utf-8")

		# Ensure index.md exists before agent runs.
		index_path = resolved_memory_root / "index.md"
		if not index_path.exists():
			index_path.write_text("# Memory Index\n", encoding="utf-8")

		# Create the ExtractAgent module and run with retry + fallback.
		agent = ExtractAgent(
			memory_root=resolved_memory_root,
			trace_path=trace_file,
			run_folder=run_folder,
			max_iters=self.config.agent_role.max_iters_sync,
		)
		history_start = len(getattr(self._lead_lm, "history", []) or [])
		start_cost_tracking()
		try:
			prediction = self._run_with_fallback(
				flow="sync",
				module=agent,
				input_args={},
			)

			capture_dspy_cost(self._lead_lm, history_start)
			cost_usd = stop_cost_tracking()
		except Exception:
			stop_cost_tracking()  # clean up accumulator
			raise

		# Extract completion summary from prediction.
		response_text = str(
			getattr(prediction, "completion_summary", "") or ""
		).strip() or "(no response)"

		# Write agent response text.
		_write_text_with_newline(artifact_paths["agent_log"], response_text)

		# Save agent trace from prediction trajectory.
		agent_trace_path = run_folder / "agent_trace.json"
		try:
			trajectory = getattr(prediction, "trajectory", {}) or {}
			trace_data = _trajectory_to_trace_list(trajectory)
			agent_trace_path.write_text(
				json.dumps(trace_data, default=str, indent=2),
				encoding="utf-8",
			)
		except Exception as exc:
			logger.warning(
				"[sync] Failed to write agent trace: {}", exc
			)
			agent_trace_path.write_text("[]", encoding="utf-8")

		payload = {
			"trace_path": str(trace_file),
			"memory_root": str(resolved_memory_root),
			"workspace_root": str(resolved_workspace_root),
			"run_folder": str(run_folder),
			"artifacts": {
				key: str(path) for key, path in artifact_paths.items()
			},
			"cost_usd": cost_usd,
		}
		return SyncResultContract.model_validate(payload).model_dump(
			mode="json"
		)

	# ------------------------------------------------------------------
	# Maintain flow
	# ------------------------------------------------------------------

	def maintain(
		self,
		memory_root: str | Path | None = None,
		workspace_root: str | Path | None = None,
	) -> dict[str, Any]:
		"""Run memory maintenance flow and return stable contract payload.

		Run memory maintenance and return a MaintainResultContract-validated dict.

		Args:
			memory_root: Override for the memory directory.
			workspace_root: Override for the workspace directory.

		Returns:
			Validated MaintainResultContract payload dict.

		Raises:
			RuntimeError: On agent failure or missing artifacts.
		"""
		repo_root = Path(self._default_cwd or Path.cwd()).expanduser().resolve()
		return self._maintain_inner(repo_root, memory_root, workspace_root)

	def _maintain_inner(
		self,
		repo_root: Path,
		memory_root: str | Path | None,
		workspace_root: str | Path | None,
	) -> dict[str, Any]:
		"""Inner maintain logic called by maintain()."""
		resolved_memory_root, resolved_workspace_root = _resolve_runtime_roots(
			config=self.config,
			memory_root=memory_root,
			workspace_root=workspace_root,
		)
		run_folder = resolved_workspace_root / _default_run_folder_name("maintain")
		run_folder.mkdir(parents=True, exist_ok=True)
		artifact_paths = build_maintain_artifact_paths(run_folder)

		# Ensure index.md exists before agent runs.
		index_path = resolved_memory_root / "index.md"
		if not index_path.exists():
			index_path.write_text("# Memory Index\n", encoding="utf-8")

		# Create the MaintainAgent module and run with retry + fallback.
		agent = MaintainAgent(
			memory_root=resolved_memory_root,
			max_iters=self.config.agent_role.max_iters_maintain,
		)
		history_start = len(getattr(self._lead_lm, "history", []) or [])
		start_cost_tracking()
		try:
			prediction = self._run_with_fallback(
				flow="maintain",
				module=agent,
				input_args={},
			)

			capture_dspy_cost(self._lead_lm, history_start)
			cost_usd = stop_cost_tracking()
		except Exception:
			stop_cost_tracking()  # clean up accumulator
			raise

		# Extract completion summary from prediction.
		response_text = str(
			getattr(prediction, "completion_summary", "") or ""
		).strip() or "(no response)"

		# Write agent response text.
		_write_text_with_newline(artifact_paths["agent_log"], response_text)

		# Save agent trace from prediction trajectory.
		agent_trace_path = run_folder / "agent_trace.json"
		try:
			trajectory = getattr(prediction, "trajectory", {}) or {}
			trace_data = _trajectory_to_trace_list(trajectory)
			agent_trace_path.write_text(
				json.dumps(trace_data, default=str, indent=2),
				encoding="utf-8",
			)
		except Exception as exc:
			logger.warning(
				"[maintain] Failed to write agent trace: {}", exc
			)
			agent_trace_path.write_text("[]", encoding="utf-8")

		# Log memory index path for observability.
		if index_path.exists():
			logger.info(f"[maintain] Memory index at {index_path}")

		payload = {
			"memory_root": str(resolved_memory_root),
			"workspace_root": str(resolved_workspace_root),
			"run_folder": str(run_folder),
			"artifacts": {
				key: str(path) for key, path in artifact_paths.items()
			},
			"cost_usd": cost_usd,
		}
		return MaintainResultContract.model_validate(payload).model_dump(
			mode="json"
		)

	# ------------------------------------------------------------------
	# Ask flow
	# ------------------------------------------------------------------

	def ask(
		self,
		prompt: str,
		session_id: str | None = None,
		cwd: str | None = None,
		memory_root: str | Path | None = None,
	) -> tuple[str, str, float]:
		"""Run one ask prompt via ReAct agent. Returns (response, session_id, cost_usd).

		The ask agent uses scan and read tools in read-only mode to browse
		and read memory files. It cannot modify any files.

		Args:
			prompt: The user's question.
			session_id: Optional session ID (generated if not provided).
			cwd: Working directory override.
			memory_root: Memory directory override.

		Returns:
			(response_text, session_id, cost_usd) tuple.
		"""
		resolved_memory_root = (
			Path(memory_root).expanduser().resolve()
			if memory_root
			else self.config.memory_dir
		)
		resolved_session_id = session_id or self.generate_session_id()

		# Format hints from pre-fetched search results.
		hints = format_ask_hints(hits=[], context_docs=[])

		# Create the AskAgent module and run with retry + fallback.
		agent = AskAgent(
			memory_root=resolved_memory_root,
			max_iters=self.config.agent_role.max_iters_ask,
		)
		history_start = len(getattr(self._lead_lm, "history", []) or [])
		start_cost_tracking()
		try:
			prediction = self._run_with_fallback(
				flow="ask",
				module=agent,
				input_args={
					"question": prompt,
					"hints": hints,
				},
			)

			capture_dspy_cost(self._lead_lm, history_start)
			cost_usd = stop_cost_tracking()
		except Exception:
			stop_cost_tracking()  # clean up accumulator
			raise

		response_text = str(
			getattr(prediction, "answer", "") or ""
		).strip() or "(no response)"
		return response_text, resolved_session_id, cost_usd
