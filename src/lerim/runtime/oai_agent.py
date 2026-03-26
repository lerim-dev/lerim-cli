"""OpenAI Agents SDK runtime for Lerim sync, maintain, and ask flows.

Replaces PydanticAI's LerimAgent for the sync, maintain, and ask operations,
using the OpenAI Agents SDK with Codex for filesystem operations and
LitellmModel for provider abstraction.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

import litellm
litellm.turn_off_message_logging = True
litellm.suppress_debug_info = True

from agents import Agent, Runner, set_tracing_disabled
from agents.extensions.experimental.codex import (
	CodexOptions,
	ThreadOptions,
	TurnOptions,
	codex_tool,
)
from agents.extensions.models.litellm_model import LitellmModel

from lerim.config.settings import Config, get_config
from lerim.memory.access_tracker import get_access_stats, init_access_db
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
)
from lerim.runtime.cost_tracker import start_cost_tracking, stop_cost_tracking
from lerim.runtime.oai_context import OAIRuntimeContext, build_oai_context
from lerim.runtime.oai_providers import build_codex_options, build_oai_model
from lerim.runtime.oai_tools import (
	extract_pipeline,
	summarize_pipeline,
	write_memory,
)
from lerim.runtime.prompts.oai_maintain import (
	build_oai_maintain_artifact_paths,
	build_oai_maintain_prompt,
)
from lerim.runtime.prompts.oai_ask import build_oai_ask_prompt
from lerim.runtime.prompts.oai_sync import build_oai_sync_prompt
from lerim.runtime.responses_proxy import ResponsesProxy

logger = logging.getLogger("lerim.runtime.oai")


class LerimOAIAgent:
	"""Lead runtime wrapper for OpenAI Agents SDK sync, maintain, and ask flows."""

	def __init__(
		self,
		default_cwd: str | None = None,
		config: Config | None = None,
	) -> None:
		"""Create OAI runtime with model and codex configuration.

		Args:
			default_cwd: Default working directory for path resolution.
			config: Lerim config; loaded via get_config() if not provided.
		"""
		# Disable OAI SDK tracing (exports to OpenAI servers by default).
		set_tracing_disabled(disabled=True)

		cfg = config or get_config()
		self.config = cfg
		self._default_cwd = default_cwd

		# Validate providers before building anything.
		from lerim.runtime.provider_caps import validate_provider_for_role
		validate_provider_for_role(cfg.lead_role.provider, "lead")
		validate_provider_for_role(cfg.codex_role.provider, "codex", cfg.codex_role.model)

		# Store codex role config for runtime parameters.
		self._codex_role = cfg.codex_role

		# Build lead model via LitellmModel
		self._lead_model: LitellmModel = build_oai_model("lead", config=cfg)

		# Build codex options (may require a proxy for non-Responses-API providers)
		self._codex_opts: dict
		self._thread_opts: dict
		self._needs_proxy: bool
		self._codex_opts, self._thread_opts, self._needs_proxy = build_codex_options(
			config=cfg,
			codex_provider=cfg.codex_role.provider,
			codex_model=cfg.codex_role.model,
		)

		# Proxy instance created lazily but not started yet
		self._proxy: ResponsesProxy | None = None
		if self._needs_proxy:
			backend_url = self._codex_opts.get("backend_url", "")
			backend_api_key = self._codex_opts.get("backend_api_key", "")
			self._proxy = ResponsesProxy(
				backend_url=backend_url,
				api_key=backend_api_key,
			)

	@staticmethod
	def _is_within(path: Path, root: Path) -> bool:
		"""Return whether path equals or is inside root."""
		resolved = path.resolve()
		root_resolved = root.resolve()
		return resolved == root_resolved or root_resolved in resolved.parents

	def _build_agent(
		self,
		*,
		prompt: str,
		memory_root: Path,
		run_folder: Path,
		codex_opts: dict,
		thread_opts: dict,
	) -> Agent[OAIRuntimeContext]:
		"""Build the OpenAI Agents SDK Agent with codex and lerim tools."""
		clean_codex_opts = {
			k: v
			for k, v in codex_opts.items()
			if k not in ("backend_url", "backend_api_key")
		}

		# working_directory = .lerim/ parent so Codex can access both
		# memory/ (for reading/writing memories) and workspace/ (for reports).
		lerim_root = str(memory_root.parent)

		agent: Agent[OAIRuntimeContext] = Agent(
			name="LerimSync",
			instructions=prompt,
			model=self._lead_model,
			tools=[
				codex_tool(
					codex_options=CodexOptions(**clean_codex_opts),
					sandbox_mode="workspace-write",
					working_directory=lerim_root,
					skip_git_repo_check=True,
					default_thread_options=ThreadOptions(
						**thread_opts,
						additional_directories=[str(run_folder)],
					),
					default_turn_options=TurnOptions(idle_timeout_seconds=self._codex_role.idle_timeout_seconds),
				),
				write_memory,
				extract_pipeline,
				summarize_pipeline,
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

		# Prepare codex options — start proxy if needed
		codex_opts = dict(self._codex_opts)
		proxy_started = False
		try:
			if self._needs_proxy and self._proxy is not None:
				proxy_url = self._proxy.start()
				proxy_started = True
				codex_opts["base_url"] = proxy_url
				logger.info(
					"[sync] Responses proxy started at %s", proxy_url
				)

			# Build the agent
			agent = self._build_agent(
				prompt=prompt,
				memory_root=resolved_memory_root,
				run_folder=run_folder,
				codex_opts=codex_opts,
				thread_opts=self._thread_opts,
			)

			# Run with retry logic (3 attempts, exponential backoff)
			max_attempts = 3
			last_error: Exception | None = None
			response_text = ""
			result = None
			start_cost_tracking()

			for attempt in range(1, max_attempts + 1):
				try:
					logger.info(
						"[sync] OAI agent attempt %d/%d (model=%s)",
						attempt,
						max_attempts,
						self.config.lead_role.model,
					)
					result = asyncio.run(
						Runner.run(
							agent, prompt, context=ctx,
							max_turns=self.config.lead_role.max_turns_sync,
						)
					)
					response_text = str(
						result.final_output or ""
					).strip() or "(no response)"
					break
				except Exception as exc:
					last_error = exc
					error_msg = str(exc)
					if "429" in error_msg or "rate limit" in error_msg.lower():
						logger.warning(
							"[sync] Rate limited on attempt %d: %s",
							attempt,
							error_msg[:100],
						)
					elif "500" in error_msg or "503" in error_msg:
						logger.warning(
							"[sync] Server error on attempt %d: %s",
							attempt,
							error_msg[:100],
						)
					elif attempt < max_attempts:
						logger.warning(
							"[sync] Error on attempt %d (%s): %s",
							attempt,
							type(exc).__name__,
							error_msg[:100],
						)
					if attempt < max_attempts:
						wait_time = min(2**attempt, 8)
						logger.info(f"[sync] Retrying in {wait_time}s...")
						time.sleep(wait_time)

			cost_usd = stop_cost_tracking()

			if result is None:
				raise RuntimeError(
					f"[sync] Failed after {max_attempts} attempts. "
					f"Last error: {last_error}"
				) from last_error

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

			# Validate required artifacts exist
			for key in ("extract", "summary", "memory_actions"):
				if not artifact_paths[key].exists():
					raise RuntimeError(
						f"missing_artifact:{artifact_paths[key]}"
					)

			# Extract summary path from summary artifact
			try:
				summary_artifact = json.loads(
					artifact_paths["summary"].read_text(encoding="utf-8")
				)
			except json.JSONDecodeError as exc:
				raise RuntimeError(
					f"invalid_json_artifact:{artifact_paths['summary']}"
				) from exc
			raw_summary = str(
				(
					summary_artifact
					if isinstance(summary_artifact, dict)
					else {}
				).get("summary_path", "")
			).strip()
			if not raw_summary:
				raise RuntimeError("missing_summary_path_in_pipeline_output")
			summary_path_resolved = Path(raw_summary).resolve()
			if not self._is_within(
				summary_path_resolved, resolved_memory_root
			):
				raise RuntimeError(
					f"summary_path_outside_memory_root:{summary_path_resolved}"
				)
			if not summary_path_resolved.exists():
				raise RuntimeError(
					f"summary_path_not_found:{summary_path_resolved}"
				)

			# Parse memory_actions report for counts and paths
			report = _load_json_dict_artifact(
				artifact_paths["memory_actions"]
			)
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
				"summary_path": str(summary_path_resolved),
				"cost_usd": cost_usd,
			}
			return SyncResultContract.model_validate(payload).model_dump(
				mode="json"
			)

		finally:
			# Always stop the proxy if we started it
			if proxy_started and self._proxy is not None:
				try:
					self._proxy.stop()
					logger.info("[sync] Responses proxy stopped")
				except Exception as exc:
					logger.warning(
						"[sync] Failed to stop proxy: %s", exc
					)

	# ------------------------------------------------------------------
	# Maintain flow
	# ------------------------------------------------------------------

	def _build_maintain_agent(
		self,
		*,
		prompt: str,
		memory_root: Path,
		run_folder: Path,
		codex_opts: dict,
		thread_opts: dict,
	) -> Agent[OAIRuntimeContext]:
		"""Build the OAI Agent for the maintain flow.

		The maintain agent only has codex_tool + write_memory. No
		extract/summarize pipelines — those are sync-only.
		"""
		clean_codex_opts = {
			k: v
			for k, v in codex_opts.items()
			if k not in ("backend_url", "backend_api_key")
		}

		# working_directory = .lerim/ parent so Codex can access both
		# memory/ (for reading/editing memories) and workspace/ (for reports).
		lerim_root = str(memory_root.parent)

		agent: Agent[OAIRuntimeContext] = Agent(
			name="LerimMaintain",
			instructions=prompt,
			model=self._lead_model,
			tools=[
				codex_tool(
					codex_options=CodexOptions(**clean_codex_opts),
					sandbox_mode="workspace-write",
					working_directory=lerim_root,
					skip_git_repo_check=True,
					default_thread_options=ThreadOptions(
						**thread_opts,
						additional_directories=[str(run_folder)],
					),
					default_turn_options=TurnOptions(idle_timeout_seconds=self._codex_role.idle_timeout_seconds),
				),
				write_memory,
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

		# Prepare codex options — start proxy if needed
		codex_opts = dict(self._codex_opts)
		proxy_started = False
		try:
			if self._needs_proxy and self._proxy is not None:
				proxy_url = self._proxy.start()
				proxy_started = True
				codex_opts["base_url"] = proxy_url
				logger.info(
					"[maintain] Responses proxy started at %s", proxy_url
				)

			# Build the maintain agent (codex + write_memory only)
			agent = self._build_maintain_agent(
				prompt=prompt,
				memory_root=resolved_memory_root,
				run_folder=run_folder,
				codex_opts=codex_opts,
				thread_opts=self._thread_opts,
			)

			# Run with retry logic (3 attempts, exponential backoff)
			max_attempts = 3
			last_error: Exception | None = None
			response_text = ""
			result = None
			start_cost_tracking()

			for attempt in range(1, max_attempts + 1):
				try:
					logger.info(
						"[maintain] OAI agent attempt %d/%d (model=%s)",
						attempt,
						max_attempts,
						self.config.lead_role.model,
					)
					result = asyncio.run(
						Runner.run(
							agent, prompt, context=ctx,
							max_turns=self.config.lead_role.max_turns_maintain,
						)
					)
					response_text = str(
						result.final_output or ""
					).strip() or "(no response)"
					break
				except Exception as exc:
					last_error = exc
					error_msg = str(exc)
					if "429" in error_msg or "rate limit" in error_msg.lower():
						logger.warning(
							"[maintain] Rate limited on attempt %d: %s",
							attempt,
							error_msg[:100],
						)
					elif "500" in error_msg or "503" in error_msg:
						logger.warning(
							"[maintain] Server error on attempt %d: %s",
							attempt,
							error_msg[:100],
						)
					elif attempt < max_attempts:
						logger.warning(
							"[maintain] Error on attempt %d (%s): %s",
							attempt,
							type(exc).__name__,
							error_msg[:100],
						)
					if attempt < max_attempts:
						wait_time = min(2**attempt, 8)
						logger.info(f"[maintain] Retrying in {wait_time}s...")
						time.sleep(wait_time)

			cost_usd = stop_cost_tracking()

			if result is None:
				raise RuntimeError(
					f"[maintain] Failed after {max_attempts} attempts. "
					f"Last error: {last_error}"
				) from last_error

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

			# Validate maintain_actions artifact exists
			actions_path = artifact_paths["maintain_actions"]
			if not actions_path.exists():
				raise RuntimeError(f"missing_artifact:{actions_path}")

			# Parse maintain_actions report for counts
			report = _load_json_dict_artifact(actions_path)
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
							raise RuntimeError(
								f"maintain_action_path_outside_allowed_roots:"
								f"{path_key}={resolved}"
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
			# Always stop the proxy if we started it
			if proxy_started and self._proxy is not None:
				try:
					self._proxy.stop()
					logger.info("[maintain] Responses proxy stopped")
				except Exception as exc:
					logger.warning(
						"[maintain] Failed to stop proxy: %s", exc
					)

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
		memory_root: Path,
		codex_opts: dict,
		thread_opts: dict,
	) -> Agent[OAIRuntimeContext]:
		"""Build the OAI Agent for the ask flow.

		Read-only Codex sandbox — the ask agent can search/read memories
		but cannot modify them.
		"""
		clean_codex_opts = {
			k: v
			for k, v in codex_opts.items()
			if k not in ("backend_url", "backend_api_key")
		}

		agent: Agent[OAIRuntimeContext] = Agent(
			name="LerimAsk",
			instructions=prompt,
			model=self._lead_model,
			tools=[
				codex_tool(
					codex_options=CodexOptions(**clean_codex_opts),
					sandbox_mode="read-only",
					working_directory=str(memory_root.parent),
					skip_git_repo_check=True,
					default_thread_options=ThreadOptions(**thread_opts),
					default_turn_options=TurnOptions(idle_timeout_seconds=self._codex_role.idle_timeout_seconds),
				),
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

		# Prepare codex options — start proxy if needed
		codex_opts = dict(self._codex_opts)
		proxy_started = False
		try:
			if self._needs_proxy and self._proxy is not None:
				proxy_url = self._proxy.start()
				proxy_started = True
				codex_opts["base_url"] = proxy_url

			agent = self._build_ask_agent(
				prompt=ask_prompt,
				memory_root=resolved_memory_root,
				codex_opts=codex_opts,
				thread_opts=self._thread_opts,
			)

			start_cost_tracking()
			result = asyncio.run(
				Runner.run(
					agent, ask_prompt, context=ctx,
					max_turns=self.config.lead_role.max_turns_ask,
				)
			)
			cost_usd = stop_cost_tracking()

			response_text = str(
				result.final_output or ""
			).strip() or "(no response)"
			return response_text, resolved_session_id, cost_usd

		finally:
			if proxy_started and self._proxy is not None:
				try:
					self._proxy.stop()
				except Exception as exc:
					logger.warning(f"[ask] Failed to stop proxy: {exc}")
