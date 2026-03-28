"""OpenAI Agents SDK runtime for Lerim sync, maintain, and ask flows.

Replaces PydanticAI's LerimAgent for the sync, maintain, and ask operations,
using the OpenAI Agents SDK with LitellmModel for provider abstraction.
Codex is used only for the ask flow (read-only filesystem access).
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

import litellm
litellm.turn_off_message_logging = True
litellm.suppress_debug_info = True

import logfire  # noqa: E402
from agents import Agent, Runner, set_tracing_disabled  # noqa: E402
from agents.extensions.models.litellm_model import LitellmModel  # noqa: E402

from lerim.config.settings import Config, get_config  # noqa: E402
from lerim.memory.access_tracker import get_access_stats, init_access_db  # noqa: E402
from lerim.runtime.helpers import (  # noqa: E402
	MaintainResultContract,
	SyncResultContract,
	_build_artifact_paths,
	_default_run_folder_name,
	_extract_counts,
	_load_json_dict_artifact,
	_resolve_runtime_roots,
	_write_json_artifact,
	_write_text_with_newline,
)
from lerim.runtime.cost_tracker import start_cost_tracking, stop_cost_tracking  # noqa: E402
from lerim.runtime.oai_context import OAIRuntimeContext, build_oai_context  # noqa: E402
from lerim.runtime.oai_providers import build_oai_fallback_models, build_oai_model  # noqa: E402
from lerim.runtime.oai_tools import (  # noqa: E402
	archive_memory,
	batch_dedup_candidates,
	edit_memory,
	extract_pipeline,
	list_files,
	memory_search,
	read_file,
	summarize_pipeline,
	write_hot_memory,
	write_memory,
	write_report,
)
from lerim.runtime.prompts.oai_maintain import (  # noqa: E402
	build_oai_maintain_artifact_paths,
	build_oai_maintain_prompt,
)
from lerim.runtime.prompts.oai_ask import build_oai_ask_prompt  # noqa: E402
from lerim.runtime.prompts.oai_sync import build_oai_sync_prompt  # noqa: E402

logger = logging.getLogger("lerim.runtime.oai")

# ---------------------------------------------------------------------------
# Persistent asyncio event loop (shared across all LerimOAIAgent instances)
# ---------------------------------------------------------------------------
# Instead of asyncio.run() per call (which creates and destroys a loop each
# time, causing "Task was destroyed but pending", "Event loop is closed", and
# ConnectionResetError issues), we keep a single loop alive in a daemon thread
# and submit coroutines to it with run_coroutine_threadsafe().
# ---------------------------------------------------------------------------

_loop: asyncio.AbstractEventLoop | None = None
_loop_thread: threading.Thread | None = None
_loop_lock = threading.Lock()


def _get_event_loop() -> asyncio.AbstractEventLoop:
	"""Get or create the persistent event loop running in a background thread."""
	global _loop, _loop_thread
	with _loop_lock:
		if _loop is not None and _loop.is_running():
			return _loop
		_loop = asyncio.new_event_loop()
		_loop_thread = threading.Thread(
			target=_loop.run_forever,
			daemon=True,
			name="lerim-async-loop",
		)
		_loop_thread.start()
		return _loop


def _run_async(coro):
	"""Run an async coroutine on the persistent event loop and wait for result.

	Propagates OpenTelemetry context from the calling thread so child spans
	(OAI Agent, LLM calls, tool executions) nest under the caller's active
	span (e.g. sync_cycle > process_session > oai_sync > Agent).
	"""
	loop = _get_event_loop()
	try:
		from opentelemetry import context as otel_context
		parent_ctx = otel_context.get_current()
	except ImportError:
		parent_ctx = None

	if parent_ctx is not None:
		async def _with_otel_ctx():
			token = otel_context.attach(parent_ctx)
			try:
				return await coro
			finally:
				otel_context.detach(token)
		future = asyncio.run_coroutine_threadsafe(_with_otel_ctx(), loop)
	else:
		future = asyncio.run_coroutine_threadsafe(coro, loop)
	return future.result()  # blocks until done


class LerimOAIAgent:
	"""Lead runtime wrapper for OpenAI Agents SDK sync, maintain, and ask flows."""

	def __init__(
		self,
		default_cwd: str | None = None,
		config: Config | None = None,
	) -> None:
		"""Create OAI runtime with model configuration.

		Args:
			default_cwd: Default working directory for path resolution.
			config: Lerim config; loaded via get_config() if not provided.
		"""
		# Only disable OAI SDK tracing when Logfire tracing is not active.
		# When tracing IS enabled, configure_tracing() installs the Logfire
		# wrapper (instrument_openai_agents) which routes OAI spans to Logfire
		# instead of OpenAI servers.
		cfg = config or get_config()
		if not cfg.tracing_enabled:
			set_tracing_disabled(disabled=True)

		# Ensure Codex CLI has a config that disables WebSocket transport.
		# The proxy is HTTP-only; Codex's default WebSocket mode causes 404s.
		_codex_config = Path.home() / ".codex" / "config.toml"
		if not _codex_config.exists():
			try:
				_codex_config.parent.mkdir(parents=True, exist_ok=True)
				_codex_config.write_text(
					"# Auto-generated by lerim — disables WebSocket for proxy compatibility\n"
					"suppress_unstable_features_warning = true\n"
				)
			except OSError:
				pass  # read-only FS — tmpfs might not be mounted yet

		cfg = config or get_config()
		self.config = cfg
		self._default_cwd = default_cwd

		# Validate lead provider.
		from lerim.runtime.provider_caps import validate_provider_for_role
		validate_provider_for_role(cfg.lead_role.provider, "lead")

		# Build lead model via LitellmModel
		self._lead_model: LitellmModel = build_oai_model("lead", config=cfg)

		# Build fallback models from config (e.g. fallback_models = ["minimax:minimax-m2.5"])
		self._fallback_models: list[LitellmModel] = build_oai_fallback_models(
			cfg.lead_role, config=cfg,
		)

	@staticmethod
	def _is_within(path: Path, root: Path) -> bool:
		"""Return whether path equals or is inside root."""
		resolved = path.resolve()
		root_resolved = root.resolve()
		return resolved == root_resolved or root_resolved in resolved.parents

	@staticmethod
	def _is_quota_error(error_msg: str) -> bool:
		"""Return True if the error message indicates a quota/rate-limit error."""
		lower = error_msg.lower()
		return "429" in error_msg or "rate limit" in lower or "quota" in lower

	def _run_with_fallback(
		self,
		*,
		flow: str,
		build_agent_fn,
		prompt: str,
		ctx: OAIRuntimeContext,
		max_turns: int,
		max_attempts: int = 3,
	):
		"""Run an agent with retry + fallback model support.

		Tries the primary model first with up to max_attempts retries.
		On quota/rate-limit errors, switches to the next fallback model.
		Non-quota errors retry the same model with exponential backoff.

		Args:
			flow: Flow name for log messages (e.g. "sync", "maintain", "ask").
			build_agent_fn: Callable(model) -> Agent that builds a fresh agent
				with the given LitellmModel.
			prompt: The prompt to pass to Runner.run.
			ctx: The OAIRuntimeContext.
			max_turns: Maximum agent turns.
			max_attempts: Retry attempts per model.

		Returns:
			The Runner.run result object.

		Raises:
			RuntimeError: If all models and attempts are exhausted.
		"""
		models = [self._lead_model] + self._fallback_models
		last_error: Exception | None = None

		for model_idx, model in enumerate(models):
			agent = build_agent_fn(model)
			model_label = self.config.lead_role.model if model_idx == 0 else f"fallback-{model_idx}"
			for attempt in range(1, max_attempts + 1):
				try:
					logger.info(f"[{flow}] OAI agent attempt {attempt}/{max_attempts} (model={model_label})")
					result = _run_async(
						Runner.run(agent, prompt, context=ctx, max_turns=max_turns)
					)
					if model_idx > 0:
						logger.info(f"[{flow}] Succeeded with fallback model {model_idx}")
					return result
				except Exception as exc:
					last_error = exc
					error_msg = str(exc)

					if self._is_quota_error(error_msg):
						logger.warning(f"[{flow}] Quota/rate-limit on attempt {attempt}: {error_msg[:100]}")
						if model_idx < len(models) - 1:
							fb_label = (
								self.config.lead_role.fallback_models[model_idx]
								if model_idx < len(self.config.lead_role.fallback_models)
								else f"fallback-{model_idx + 1}"
							)
							logger.warning(f"[{flow}] Switching to fallback: {fb_label}")
							break  # Break inner retry loop, try next model
						# Last model — keep retrying with backoff
					elif "500" in error_msg or "503" in error_msg:
						logger.warning(f"[{flow}] Server error on attempt {attempt}: {error_msg[:100]}")
					elif attempt < max_attempts:
						logger.warning(f"[{flow}] Error on attempt {attempt} ({type(exc).__name__}): {error_msg[:100]}")

					if attempt < max_attempts:
						wait_time = min(2**attempt, 8)
						logger.info(f"[{flow}] Retrying in {wait_time}s...")
						time.sleep(wait_time)
			else:
				# Inner loop exhausted all attempts without breaking — no more retries for this model
				continue
			# Inner loop broke (quota error, switching to next model) — continue outer loop
			continue

		raise RuntimeError(
			f"[{flow}] Failed after trying {len(models)} model(s). "
			f"Last error: {last_error}"
		) from last_error

	def _build_agent(
		self,
		*,
		prompt: str,
	) -> Agent[OAIRuntimeContext]:
		"""Build the OpenAI Agents SDK Agent with lerim tools."""
		agent: Agent[OAIRuntimeContext] = Agent(
			name="LerimSync",
			instructions=prompt,
			model=self._lead_model,
			tools=[
				extract_pipeline,
				summarize_pipeline,
				write_memory,
				write_report,
				read_file,
				list_files,
				batch_dedup_candidates,
			],
		)
		return agent

	def sync(
		self,
		trace_path: str | Path,
		memory_root: str | Path | None = None,
		workspace_root: str | Path | None = None,
	) -> dict[str, Any]:
		"""Run memory-write sync flow and return stable contract payload.

		Mirrors the existing LerimAgent.sync() contract exactly, returning
		a SyncResultContract-validated dict.

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
		with logfire.span("oai_sync", trace_path=str(trace_file), repo=repo_root.name):
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

		prompt = build_oai_sync_prompt(
			trace_file=trace_file,
			memory_root=resolved_memory_root,
			run_folder=run_folder,
			artifact_paths=artifact_paths,
			metadata=metadata,
		)

		ctx = build_oai_context(
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

		try:
			# Run with retry + fallback model support
			start_cost_tracking()

			def _build_sync_agent(model):
				ag = self._build_agent(prompt=prompt)
				ag.model = model
				return ag

			result = self._run_with_fallback(
				flow="sync",
				build_agent_fn=_build_sync_agent,
				prompt=prompt,
				ctx=ctx,
				max_turns=self.config.lead_role.max_turns_sync,
			)
			response_text = str(
				result.final_output or ""
			).strip() or "(no response)"

			cost_usd = stop_cost_tracking()

			# Write agent response text
			_write_text_with_newline(artifact_paths["agent_log"], response_text)

			# Save agent trace from result
			agent_trace_path = run_folder / "agent_trace.json"
			try:
				trace_data = result.to_input_list()
				agent_trace_path.write_text(
					json.dumps(trace_data, default=str, indent=2),
					encoding="utf-8",
				)
			except Exception as exc:
				logger.warning(
					"[sync] Failed to write agent trace: %s", exc
				)
				agent_trace_path.write_text("[]", encoding="utf-8")

			# Validate required artifacts exist.
			# extract is a hard requirement (DSPy extraction must succeed).
			# summary may fail for oversized sessions — treat as soft failure.
			# memory_actions is written by write_report tool — soft failure.
			if not artifact_paths["extract"].exists():
				raise RuntimeError(
					f"missing_artifact:{artifact_paths['extract']}"
				)
			if not artifact_paths["summary"].exists():
				logger.warning(
					"[sync] summary.json missing — summarization may have "
					"failed for oversized sessions. Continuing with extraction only."
				)
				artifact_paths["summary"].write_text(
					json.dumps({"summary_path": ""}),
					encoding="utf-8",
				)
			if not artifact_paths["memory_actions"].exists():
				logger.warning(
					"[sync] memory_actions.json missing — write_report may "
					"have been skipped, but write_memory tool may have written memories."
				)
				artifact_paths["memory_actions"].write_text(
					json.dumps({"counts": {"add": 0, "update": 0, "no_op": 0}, "written_memory_paths": [], "note": "report_not_written"}),
					encoding="utf-8",
				)

			# Extract summary path from summary artifact (soft — may be empty)
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
					if not self._is_within(
						summary_path_resolved, resolved_memory_root
					):
						logger.warning(
							"[sync] summary_path outside memory_root: %s",
							summary_path_resolved,
						)
						summary_path_resolved = None
					elif not summary_path_resolved.exists():
						logger.warning(
							"[sync] summary_path not found: %s",
							summary_path_resolved,
						)
						summary_path_resolved = None
			except (json.JSONDecodeError, Exception) as exc:
				logger.warning("[sync] Failed to parse summary artifact: {}", exc)
				summary_path_resolved = None

			# Parse memory_actions report for counts and paths.
			# Codex may write invalid JSON or text error messages instead.
			try:
				report = _load_json_dict_artifact(
					artifact_paths["memory_actions"]
				)
			except RuntimeError:
				logger.warning(
					"[sync] memory_actions.json is invalid — using empty defaults"
				)
				report = {"counts": {"add": 0, "update": 0, "no_op": 0}, "written_memory_paths": []}
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
					self._is_within(resolved, resolved_memory_root)
					or self._is_within(resolved, run_folder)
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
				"summary_path": str(summary_path_resolved) if summary_path_resolved else "",
				"cost_usd": cost_usd,
			}
			return SyncResultContract.model_validate(payload).model_dump(
				mode="json"
			)

		finally:
			pass

	# ------------------------------------------------------------------
	# Maintain flow
	# ------------------------------------------------------------------

	def _build_maintain_agent(
		self,
		*,
		prompt: str,
	) -> Agent[OAIRuntimeContext]:
		"""Build the OAI Agent for the maintain flow.

		Uses lightweight tools for all operations (archive, edit, hot-memory,
		search, merge candidate discovery).
		"""
		agent: Agent[OAIRuntimeContext] = Agent(
			name="LerimMaintain",
			instructions=prompt,
			model=self._lead_model,
			tools=[
				write_memory,
				write_report,
				read_file,
				list_files,
				archive_memory,
				edit_memory,
				write_hot_memory,
				memory_search,
			],
		)
		return agent

	def maintain(
		self,
		memory_root: str | Path | None = None,
		workspace_root: str | Path | None = None,
	) -> dict[str, Any]:
		"""Run memory maintenance flow and return stable contract payload.

		Mirrors the existing LerimAgent.maintain() contract, returning
		a MaintainResultContract-validated dict.

		Args:
			memory_root: Override for the memory directory.
			workspace_root: Override for the workspace directory.

		Returns:
			Validated MaintainResultContract payload dict.

		Raises:
			RuntimeError: On agent failure or missing artifacts.
		"""
		repo_root = Path(self._default_cwd or Path.cwd()).expanduser().resolve()
		with logfire.span("oai_maintain", repo=repo_root.name):
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
		artifact_paths = build_oai_maintain_artifact_paths(run_folder)

		# Initialize access tracking and fetch stats for decay
		init_access_db(self.config.memories_db_path)
		access_stats = get_access_stats(
			self.config.memories_db_path,
			str(resolved_memory_root),
		)

		prompt = build_oai_maintain_prompt(
			memory_root=resolved_memory_root,
			run_folder=run_folder,
			artifact_paths=artifact_paths,
			access_stats=access_stats,
			decay_days=self.config.decay_days,
			decay_archive_threshold=self.config.decay_archive_threshold,
			decay_min_confidence_floor=self.config.decay_min_confidence_floor,
			decay_recent_access_grace_days=self.config.decay_recent_access_grace_days,
		)

		ctx = build_oai_context(
			repo_root=repo_root,
			memory_root=resolved_memory_root,
			workspace_root=resolved_workspace_root,
			run_folder=run_folder,
			run_id=run_folder.name,
			config=self.config,
			artifact_paths=artifact_paths,
		)

		try:
			# Run with retry + fallback model support
			start_cost_tracking()

			def _build_maintain_agent_with_model(model):
				ag = self._build_maintain_agent(prompt=prompt)
				ag.model = model
				return ag

			result = self._run_with_fallback(
				flow="maintain",
				build_agent_fn=_build_maintain_agent_with_model,
				prompt=prompt,
				ctx=ctx,
				max_turns=self.config.lead_role.max_turns_maintain,
			)
			response_text = str(
				result.final_output or ""
			).strip() or "(no response)"

			cost_usd = stop_cost_tracking()

			# Write agent response text
			_write_text_with_newline(artifact_paths["agent_log"], response_text)

			# Save agent trace from result
			agent_trace_path = run_folder / "agent_trace.json"
			try:
				trace_data = result.to_input_list()
				agent_trace_path.write_text(
					json.dumps(trace_data, default=str, indent=2),
					encoding="utf-8",
				)
			except Exception as exc:
				logger.warning(
					"[maintain] Failed to write agent trace: %s", exc
				)
				agent_trace_path.write_text("[]", encoding="utf-8")

			# Validate maintain_actions artifact exists.
			# If write_report was skipped, write_memory may still have done work.
			actions_path = artifact_paths["maintain_actions"]
			if not actions_path.exists():
				logger.warning(
					"[maintain] maintain_actions.json missing — using empty defaults"
				)
				actions_path.write_text(
					json.dumps({"counts": {"merged": 0, "archived": 0, "consolidated": 0, "decayed": 0, "unchanged": 0}, "note": "report_not_written"}),
					encoding="utf-8",
				)

			# Parse maintain_actions report for counts
			try:
				report = _load_json_dict_artifact(actions_path)
			except RuntimeError:
				logger.warning(
					"[maintain] maintain_actions.json is invalid — using empty defaults"
				)
				report = {"counts": {"merged": 0, "archived": 0, "consolidated": 0, "decayed": 0, "unchanged": 0}}
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
							self._is_within(resolved, resolved_memory_root)
							or self._is_within(resolved, run_folder)
							or self._is_within(resolved, lerim_root)
						):
							logger.warning(
								"[maintain] action path outside allowed roots: {}={} — skipping validation for this action",
								path_key, resolved,
							)

			# Log hot-memory path for observability
			hot_memory_path = resolved_memory_root.parent / "hot-memory.md"
			if hot_memory_path.exists():
				logger.info(
					"[maintain] Hot memory written to %s", hot_memory_path
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

		finally:
			pass

	# ------------------------------------------------------------------
	# Ask flow
	# ------------------------------------------------------------------

	@staticmethod
	def generate_session_id() -> str:
		"""Generate a unique session ID for ask mode."""
		import secrets
		return f"lerim-{secrets.token_hex(6)}"

	def _build_ask_agent(
		self,
		*,
		prompt: str,
	) -> Agent[OAIRuntimeContext]:
		"""Build the OAI Agent for the ask flow.

		Read-only: the ask agent can search and read memories
		but cannot modify them. Uses memory_search for hybrid
		FTS5+vector search and read_file for detail reads.
		"""
		agent: Agent[OAIRuntimeContext] = Agent(
			name="LerimAsk",
			instructions=prompt,
			model=self._lead_model,
			tools=[
				memory_search,
				read_file,
				list_files,
			],
		)
		return agent

	def ask(
		self,
		prompt: str,
		session_id: str | None = None,
		cwd: str | None = None,
		memory_root: str | Path | None = None,
	) -> tuple[str, str, float]:
		"""Run one ask prompt via OAI agent. Returns (response, session_id, cost_usd).

		The ask agent uses Codex in read-only mode to search and read
		memory files. It cannot modify any files.

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

		ctx = build_oai_context(
			repo_root=runtime_cwd,
			memory_root=resolved_memory_root,
			run_id=resolved_session_id,
			config=self.config,
		)

		ask_prompt = build_oai_ask_prompt(
			question=prompt,
			hits=[],
			context_docs=[],
			memory_root=str(resolved_memory_root),
		)

		# Run with retry + fallback model support
		start_cost_tracking()

		def _build_ask_agent_with_model(model):
			ag = self._build_ask_agent(prompt=ask_prompt)
			ag.model = model
			return ag

		result = self._run_with_fallback(
			flow="ask",
			build_agent_fn=_build_ask_agent_with_model,
			prompt=ask_prompt,
			ctx=ctx,
			max_turns=self.config.lead_role.max_turns_ask,
		)
		cost_usd = stop_cost_tracking()

		response_text = str(
			result.final_output or ""
		).strip() or "(no response)"
		return response_text, resolved_session_id, cost_usd
