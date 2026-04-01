"""DSPy ReAct runtime for Lerim sync, maintain, and ask flows.

Synchronous DSPy ReAct orchestrator. Creates DSPy modules (SyncAgent,
MaintainAgent, AskAgent) per call and runs them via dspy.context(lm=...)
for thread-safe model switching.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import dspy
import logfire

from lerim.config.settings import Config, get_config
from lerim.memory.access_tracker import get_access_stats, init_access_db
from lerim.runtime.ask_agent import AskAgent
from lerim.runtime.context import build_context
from lerim.runtime.cost_tracker import (
	capture_dspy_cost,
	start_cost_tracking,
	stop_cost_tracking,
)
from lerim.runtime.helpers import (
	MaintainResultContract,
	SyncResultContract,
	_build_artifact_paths,
	_default_run_folder_name,
	_extract_counts,
	_load_json_dict_artifact,
	_resolve_runtime_roots,
	_write_json_artifact,
	_write_text_with_newline,
	build_maintain_artifact_paths,
	is_within,
)
from lerim.runtime.ask_agent import format_ask_hints
from lerim.runtime.maintain_agent import MaintainAgent, format_access_stats_section
from lerim.runtime.providers import build_dspy_fallback_lms, build_dspy_lm
from lerim.runtime.sync_agent import SyncAgent

logger = logging.getLogger("lerim.runtime")


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
	"""Lead runtime orchestrator for DSPy ReAct sync, maintain, and ask flows."""

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

		# Validate lead provider.
		from lerim.runtime.provider_caps import validate_provider_for_role
		validate_provider_for_role(cfg.lead_role.provider, "lead")

		# Build lead LM via DSPy provider builder.
		self._lead_lm: dspy.LM = build_dspy_lm("lead", config=cfg)

		# Build fallback LMs from config (e.g. fallback_models = ["minimax:minimax-m2.5"]).
		self._fallback_lms: list[dspy.LM] = build_dspy_fallback_lms(
			"lead", config=cfg,
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
			module: The DSPy module to call (SyncAgent, MaintainAgent, AskAgent).
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
				self.config.lead_role.model
				if model_idx == 0
				else f"fallback-{model_idx}"
			)
			for attempt in range(1, max_attempts + 1):
				try:
					logger.info(
						f"[{flow}] ReAct attempt {attempt}/{max_attempts} "
						f"(model={model_label})"
					)
					with dspy.context(lm=lm):
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
								self.config.lead_role.fallback_models[model_idx]
								if model_idx < len(self.config.lead_role.fallback_models)
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
		with logfire.span("sync_agent", trace_path=str(trace_file), repo=repo_root.name):
			return self._sync_inner(trace_file, repo_root, memory_root, workspace_root)

	def _sync_inner(
		self,
		trace_file: Path,
		repo_root: Path,
		memory_root: str | Path | None,
		workspace_root: str | Path | None,
	) -> dict[str, Any]:
		"""Inner sync logic wrapped by a Logfire span in sync()."""
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

		# Build RuntimeContext for tool functions.
		ctx = build_context(
			repo_root=repo_root,
			memory_root=resolved_memory_root,
			workspace_root=resolved_workspace_root,
			run_folder=run_folder,
			extra_read_roots=(trace_file.parent,),
			run_id=run_folder.name,
			config=self.config,
			trace_path=trace_file,
			artifact_paths=artifact_paths,
		)

		# Create the SyncAgent module and run with retry + fallback.
		agent = SyncAgent(ctx)
		history_start = len(getattr(self._lead_lm, "history", []) or [])
		start_cost_tracking()
		try:
			with logfire.span("sync_react_run"):
				prediction = self._run_with_fallback(
					flow="sync",
					module=agent,
					input_args={
						"trace_path": str(trace_file),
						"memory_root": str(resolved_memory_root),
						"run_folder": str(run_folder),
						"extract_artifact_path": str(artifact_paths["extract"]),
						"memory_actions_path": str(artifact_paths["memory_actions"]),
						"run_id": metadata.get("run_id", ""),
					},
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

		# Validate required artifacts exist.
		# extract is a hard requirement (DSPy extraction must succeed).
		# summary may fail for oversized sessions -- treat as soft failure.
		# memory_actions is written by write_report tool -- soft failure.
		if not artifact_paths["extract"].exists():
			raise RuntimeError(
				f"missing_artifact:{artifact_paths['extract']}"
			)
		if not artifact_paths["summary"].exists():
			logger.warning(
				"[sync] summary.json missing -- summarization may have "
				"failed for oversized sessions. Continuing with extraction only."
			)
			artifact_paths["summary"].write_text(
				json.dumps({"summary_path": ""}),
				encoding="utf-8",
			)
		if not artifact_paths["memory_actions"].exists():
			logger.warning(
				"[sync] memory_actions.json missing -- write_report may "
				"have been skipped, but write_memory tool may have written memories."
			)
			artifact_paths["memory_actions"].write_text(
				json.dumps({
					"counts": {"add": 0, "update": 0, "no_op": 0},
					"written_memory_paths": [],
					"note": "report_not_written",
				}),
				encoding="utf-8",
			)

		# Extract summary path from summary artifact (soft -- may be empty).
		summary_path_resolved = None
		try:
			summary_artifact = json.loads(
				artifact_paths["summary"].read_text(encoding="utf-8")
			)
			raw_summary = str(
				(
					summary_artifact
					if isinstance(summary_artifact, dict)
					else {}
				).get("summary_path", "")
			).strip()
			if raw_summary:
				summary_path_resolved = Path(raw_summary).resolve()
				if not is_within(
					summary_path_resolved, resolved_memory_root
				):
					logger.warning(
						"[sync] summary_path outside memory_root: {}",
						summary_path_resolved,
					)
					summary_path_resolved = None
				elif not summary_path_resolved.exists():
					logger.warning(
						"[sync] summary_path not found: {}",
						summary_path_resolved,
					)
					summary_path_resolved = None
		except (json.JSONDecodeError, Exception) as exc:
			logger.warning("[sync] Failed to parse summary artifact: {}", exc)
			summary_path_resolved = None

		# Parse memory_actions report for counts and paths.
		# Agent may write invalid JSON or text error messages instead.
		try:
			report = _load_json_dict_artifact(
				artifact_paths["memory_actions"]
			)
		except RuntimeError:
			logger.warning(
				"[sync] memory_actions.json is invalid -- using empty defaults"
			)
			report = {
				"counts": {"add": 0, "update": 0, "no_op": 0},
				"written_memory_paths": [],
			}
		counts_field = report.get("counts")
		counts_raw = counts_field if isinstance(counts_field, dict) else {}
		counts = _extract_counts(
			counts_raw,
			{
				"add": ("add",),
				"update": ("update",),
				"no_op": ("no_op", "no-op"),
			},
		)

		written_memory_paths: list[str] = []
		for item in report.get("written_memory_paths") or []:
			if not isinstance(item, str) or not item:
				continue
			resolved = Path(item).resolve()
			if not (
				is_within(resolved, resolved_memory_root)
				or is_within(resolved, run_folder)
			):
				raise RuntimeError(
					f"report_path_outside_allowed_roots:{resolved}"
				)
			written_memory_paths.append(str(resolved))

		payload = {
			"trace_path": str(trace_file),
			"memory_root": str(resolved_memory_root),
			"workspace_root": str(resolved_workspace_root),
			"run_folder": str(run_folder),
			"artifacts": {
				key: str(path) for key, path in artifact_paths.items()
			},
			"counts": counts,
			"written_memory_paths": written_memory_paths,
			"summary_path": (
				str(summary_path_resolved) if summary_path_resolved else ""
			),
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
		with logfire.span("maintain_agent", repo=repo_root.name):
			return self._maintain_inner(repo_root, memory_root, workspace_root)

	def _maintain_inner(
		self,
		repo_root: Path,
		memory_root: str | Path | None,
		workspace_root: str | Path | None,
	) -> dict[str, Any]:
		"""Inner maintain logic wrapped by a Logfire span in maintain()."""
		resolved_memory_root, resolved_workspace_root = _resolve_runtime_roots(
			config=self.config,
			memory_root=memory_root,
			workspace_root=workspace_root,
		)
		run_folder = resolved_workspace_root / _default_run_folder_name("maintain")
		run_folder.mkdir(parents=True, exist_ok=True)
		artifact_paths = build_maintain_artifact_paths(run_folder)

		# Initialize access tracking and fetch stats for decay.
		init_access_db(self.config.memories_db_path)
		access_stats = get_access_stats(
			self.config.memories_db_path,
			str(resolved_memory_root),
		)

		# Format access stats section for the agent.
		access_stats_text = format_access_stats_section(
			access_stats,
			decay_days=self.config.decay_days,
			decay_archive_threshold=self.config.decay_archive_threshold,
			decay_min_confidence_floor=self.config.decay_min_confidence_floor,
			decay_recent_access_grace_days=self.config.decay_recent_access_grace_days,
		)

		hot_memory_path = resolved_memory_root.parent / "hot-memory.md"

		# Build RuntimeContext for tool functions.
		ctx = build_context(
			repo_root=repo_root,
			memory_root=resolved_memory_root,
			workspace_root=resolved_workspace_root,
			run_folder=run_folder,
			run_id=run_folder.name,
			config=self.config,
			artifact_paths=artifact_paths,
		)

		# Create the MaintainAgent module and run with retry + fallback.
		agent = MaintainAgent(ctx)
		history_start = len(getattr(self._lead_lm, "history", []) or [])
		start_cost_tracking()
		try:
			with logfire.span("maintain_react_run"):
				prediction = self._run_with_fallback(
					flow="maintain",
					module=agent,
					input_args={
						"memory_root": str(resolved_memory_root),
						"run_folder": str(run_folder),
						"maintain_actions_path": str(artifact_paths["maintain_actions"]),
						"hot_memory_path": str(hot_memory_path),
						"access_stats": access_stats_text,
					},
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

		# Validate maintain_actions artifact exists.
		# If write_report was skipped, write_memory may still have done work.
		actions_path = artifact_paths["maintain_actions"]
		if not actions_path.exists():
			logger.warning(
				"[maintain] maintain_actions.json missing -- using empty defaults"
			)
			actions_path.write_text(
				json.dumps({
					"counts": {
						"merged": 0,
						"archived": 0,
						"consolidated": 0,
						"decayed": 0,
						"unchanged": 0,
					},
					"note": "report_not_written",
				}),
				encoding="utf-8",
			)

		# Parse maintain_actions report for counts.
		try:
			report = _load_json_dict_artifact(actions_path)
		except RuntimeError:
			logger.warning(
				"[maintain] maintain_actions.json is invalid -- using empty defaults"
			)
			report = {
				"counts": {
					"merged": 0,
					"archived": 0,
					"consolidated": 0,
					"decayed": 0,
					"unchanged": 0,
				},
			}
		counts_field = report.get("counts")
		counts_raw = counts_field if isinstance(counts_field, dict) else {}
		counts = _extract_counts(
			counts_raw,
			{
				"merged": ("merged",),
				"archived": ("archived",),
				"consolidated": ("consolidated",),
				"decayed": ("decayed",),
				"unchanged": ("unchanged",),
			},
		)

		# Validate action paths are within allowed roots.
		# Allowed: memory_root, run_folder, and memory_root.parent
		# (hot-memory.md lives at .lerim/hot-memory.md which is memory_root.parent).
		lerim_root = resolved_memory_root.parent
		for action in report.get("actions") or []:
			if not isinstance(action, dict):
				continue
			for path_key in ("source_path", "target_path"):
				val = action.get(path_key)
				paths_raw: list[str] = (
					[str(v) for v in val]
					if isinstance(val, list)
					else [str(val or "").strip()]
				)
				for raw in paths_raw:
					raw = raw.strip()
					if not raw:
						continue
					resolved = Path(raw).resolve()
					if not (
						is_within(resolved, resolved_memory_root)
						or is_within(resolved, run_folder)
						or is_within(resolved, lerim_root)
					):
						logger.warning(
							"[maintain] action path outside allowed roots: "
							"{}={} -- skipping validation for this action",
							path_key, resolved,
						)

		# Log hot-memory path for observability.
		hot_memory_path = resolved_memory_root.parent / "hot-memory.md"
		if hot_memory_path.exists():
			logger.info(
				"[maintain] Hot memory written to {}", hot_memory_path
			)
		else:
			logger.info(
				"[maintain] Hot memory not written (agent may have skipped)"
			)

		payload = {
			"memory_root": str(resolved_memory_root),
			"workspace_root": str(resolved_workspace_root),
			"run_folder": str(run_folder),
			"artifacts": {
				key: str(path) for key, path in artifact_paths.items()
			},
			"counts": counts,
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

		The ask agent uses memory_search and read_file tools in read-only
		mode to search and read memory files. It cannot modify any files.

		Args:
			prompt: The user's question.
			session_id: Optional session ID (generated if not provided).
			cwd: Working directory override.
			memory_root: Memory directory override.

		Returns:
			(response_text, session_id, cost_usd) tuple.
		"""
		runtime_cwd = (
			Path(cwd or self._default_cwd or str(Path.cwd())).expanduser().resolve()
		)
		resolved_memory_root = (
			Path(memory_root).expanduser().resolve()
			if memory_root
			else self.config.memory_dir
		)
		resolved_session_id = session_id or self.generate_session_id()

		# Build RuntimeContext for tool functions (minimal -- no run_folder or
		# artifact_paths for ask flow).
		ctx = build_context(
			repo_root=runtime_cwd,
			memory_root=resolved_memory_root,
			run_id=resolved_session_id,
			config=self.config,
		)

		# Format hints from pre-fetched search results.
		hints = format_ask_hints(hits=[], context_docs=[])

		# Create the AskAgent module and run with retry + fallback.
		agent = AskAgent(ctx)
		history_start = len(getattr(self._lead_lm, "history", []) or [])
		start_cost_tracking()
		try:
			prediction = self._run_with_fallback(
				flow="ask",
				module=agent,
				input_args={
					"question": prompt,
					"memory_root": str(resolved_memory_root),
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
