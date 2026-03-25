"""OpenAI Agents SDK runtime for Lerim sync flow.

Replaces PydanticAI's LerimAgent for the sync operation, using the
OpenAI Agents SDK with Codex for filesystem operations and LitellmModel
for provider abstraction.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

from agents import Agent, Runner, set_tracing_disabled
from agents.extensions.models.litellm_model import LitellmModel

# Disable OAI SDK tracing — it exports to OpenAI servers using OPENAI_API_KEY.
# Lerim uses its own tracing via Logfire.
set_tracing_disabled(disabled=True)
from agents.extensions.experimental.codex import (
	CodexOptions,
	ThreadOptions,
	TurnOptions,
	codex_tool,
)

from lerim.config.settings import Config, get_config
from lerim.runtime.agent import (
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
from lerim.runtime.prompts.oai_sync import build_oai_sync_prompt
from lerim.runtime.responses_proxy import ResponsesProxy

logger = logging.getLogger("lerim.runtime.oai")


class LerimOAIAgent:
	"""Lead runtime wrapper for the OpenAI Agents SDK sync flow."""

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
		cfg = config or get_config()
		self.config = cfg
		self._default_cwd = default_cwd

		# Build lead model via LitellmModel
		self._lead_model: LitellmModel = build_oai_model("lead", config=cfg)

		# Build codex options (may require a proxy for non-Responses-API providers)
		self._codex_opts: dict
		self._thread_opts: dict
		self._needs_proxy: bool
		self._codex_opts, self._thread_opts, self._needs_proxy = build_codex_options(
			config=cfg
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
		codex_opts: dict,
		thread_opts: dict,
	) -> Agent[OAIRuntimeContext]:
		"""Build the OpenAI Agents SDK Agent with codex and lerim tools."""
		# Clean codex_opts: remove proxy-construction keys that are not
		# valid CodexOptions fields.
		clean_codex_opts = {
			k: v
			for k, v in codex_opts.items()
			if k not in ("backend_url", "backend_api_key")
		}

		agent: Agent[OAIRuntimeContext] = Agent(
			name="LerimSync",
			instructions=prompt,
			model=self._lead_model,
			tools=[
				codex_tool(
					codex_options=CodexOptions(**clean_codex_opts),
					sandbox_mode="workspace-write",
					working_directory=str(memory_root),
					skip_git_repo_check=True,
					default_thread_options=ThreadOptions(**thread_opts),
					default_turn_options=TurnOptions(idle_timeout_seconds=120),
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
						Runner.run(agent, prompt, context=ctx)
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
						logger.info("[sync] Retrying in %ds...", wait_time)
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
