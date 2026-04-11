"""Runtime orchestrator for Lerim sync (PydanticAI) and maintain/ask (DSPy).

Sync uses the PydanticAI three-pass pipeline in
`lerim.agents.extract.run_extraction_three_pass`, constructed per call with a
fresh `OpenAIChatModel` built by `lerim.agents.extract_pydanticai.build_model`.
Maintain and ask still use DSPy ReAct modules (MaintainAgent, AskAgent) run
via `dspy.context(lm=...)` for thread-safe model switching.

Two retry/fallback helpers coexist until maintain/ask migrate:
- `_run_with_fallback` — PydanticAI-aware, called only by `sync()`.
- `_run_dspy_with_fallback` — DSPy string-match, called by maintain/ask.
"""

from __future__ import annotations

import json
import logging
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import dspy
from lerim.config.settings import Config, get_config
from lerim.agents.ask import AskAgent
from lerim.agents.contracts import (
	MaintainResultContract,
	SyncResultContract,
)
from lerim.agents.ask import format_ask_hints
from lerim.agents.maintain import MaintainAgent
from lerim.agents.extract import FinalizeResult, run_extraction_three_pass
from lerim.config.providers import (
	build_dspy_fallback_lms,
	build_dspy_lm,
	build_pydantic_model,
)

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
	"""Resolve memory/workspace roots using config defaults when unset.

	Workspace always resolves to the global data dir (~/.lerim/workspace).
	Memory root is per-project when passed by daemon, else falls back to config.
	"""
	resolved_memory_root = (
		Path(memory_root).expanduser().resolve() if memory_root else config.memory_dir
	)
	resolved_workspace_root = (
		Path(workspace_root).expanduser().resolve()
		if workspace_root
		else (config.global_data_dir / "workspace")
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
# PydanticAI quota error detection (for sync path)
# ---------------------------------------------------------------------------


def _is_quota_error_pydantic(exc: Exception) -> bool:
	"""Detect rate-limit / quota errors across PydanticAI provider backends.

	PydanticAI propagates provider exceptions directly, so this checks
	`openai.RateLimitError`, `openai.APIStatusError(status_code=429)`, and
	`httpx.HTTPStatusError` with a 429 response. Falls back to a string
	match for wrapped / obscured errors so provider quirks still fire the
	fallback path.
	"""
	try:
		from openai import APIStatusError, RateLimitError
	except ImportError:
		RateLimitError = APIStatusError = None
	try:
		from httpx import HTTPStatusError
	except ImportError:
		HTTPStatusError = None

	if RateLimitError is not None and isinstance(exc, RateLimitError):
		return True
	if (
		APIStatusError is not None
		and isinstance(exc, APIStatusError)
		and getattr(exc, "status_code", None) == 429
	):
		return True
	if HTTPStatusError is not None and isinstance(exc, HTTPStatusError):
		try:
			if exc.response.status_code == 429:
				return True
		except Exception:
			pass

	# String fallback for wrapped/obscured errors
	msg = str(exc).lower()
	return "429" in msg or "rate limit" in msg or "quota" in msg


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
	"""Runtime orchestrator — PydanticAI sync + DSPy maintain/ask flows."""

	def __init__(
		self,
		default_cwd: str | None = None,
		config: Config | None = None,
	) -> None:
		"""Create the runtime with model configuration.

		Sync runs PydanticAI three-pass pipeline; maintain/ask run DSPy
		ReAct modules. DSPy LMs are pre-built here for the maintain/ask path;
		sync builds its model fresh per call from config.

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
		"""Return True if the error message indicates a quota/rate-limit error.

		String-match version used by the DSPy retry path (maintain/ask).
		The PydanticAI sync path uses `_is_quota_error_pydantic` instead.
		"""
		lower = error_msg.lower()
		return "429" in error_msg or "rate limit" in lower or "quota" in lower

	@staticmethod
	def generate_session_id() -> str:
		"""Generate a unique session ID for ask mode."""
		import secrets
		return f"lerim-{secrets.token_hex(6)}"

	# ------------------------------------------------------------------
	# Retry + fallback (PydanticAI path for sync)
	# ------------------------------------------------------------------

	def _run_with_fallback(
		self,
		*,
		flow: str,
		callable_fn: Callable[[Any], Any],
		model_builders: list[Callable[[], Any]],
		max_attempts: int = 3,
	) -> Any:
		"""Run a PydanticAI callable with retry + fallback model support.

		Iterates over `model_builders` (primary first, then each fallback). For
		each builder, makes up to `max_attempts` attempts. Catches:

		- `UsageLimitExceeded`: local budget exhausted — re-raised immediately.
		- Quota/rate-limit errors (detected via `_is_quota_error_pydantic`):
		  short-circuits the current model's retry loop and switches to the
		  next builder.
		- Other exceptions: retries the same builder with exponential backoff.

		Args:
			flow: Flow name used in log messages (e.g. "sync").
			callable_fn: A callable that takes a model instance and runs the
				pipeline. Typically closes over per-call state (deps, paths).
			model_builders: Ordered list of zero-arg factories. Each must
				return a fresh `OpenAIChatModel` instance.
			max_attempts: Retry attempts per builder before moving on.

		Returns:
			Whatever `callable_fn(model)` returns on success.

		Raises:
			UsageLimitExceeded: Propagated immediately; no retry or fallback.
			RuntimeError: If all builders and attempts are exhausted.
		"""
		from pydantic_ai.exceptions import UsageLimitExceeded

		last_exc: Exception | None = None
		for model_idx, builder in enumerate(model_builders):
			model_label = (
				self.config.agent_role.model
				if model_idx == 0
				else f"fallback-{model_idx}"
			)
			for attempt in range(1, max_attempts + 1):
				try:
					logger.info(
						f"[{flow}] pydantic-ai attempt {attempt}/{max_attempts} "
						f"(model={model_label})"
					)
					model = builder()
					return callable_fn(model)
				except UsageLimitExceeded as exc:
					logger.warning(
						f"[{flow}] usage limit exceeded, short-circuiting: {exc}"
					)
					raise
				except Exception as exc:
					last_exc = exc
					if _is_quota_error_pydantic(exc):
						logger.warning(
							f"[{flow}] quota error on {model_label}: {str(exc)[:100]}"
						)
						break  # switch to next model builder
					if attempt < max_attempts:
						wait_time = min(2 ** attempt, 8)
						logger.warning(
							f"[{flow}] transient error on attempt "
							f"{attempt}/{max_attempts} ({type(exc).__name__}): "
							f"{str(exc)[:100]}; retrying in {wait_time}s..."
						)
						time.sleep(wait_time)
						continue
					logger.error(
						f"[{flow}] exhausted retries on {model_label}: "
						f"{str(exc)[:100]}"
					)
					break

		raise RuntimeError(
			f"[{flow}] Failed after trying {len(model_builders)} model(s). "
			f"Last error: {last_exc}"
		) from last_exc

	# ------------------------------------------------------------------
	# Retry + fallback (DSPy path for maintain/ask)
	# ------------------------------------------------------------------

	def _run_dspy_with_fallback(
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
			flow: Flow name for log messages (e.g. "maintain", "ask").
			module: The DSPy module to call (MaintainAgent, AskAgent).
			input_args: Keyword arguments passed to module(**input_args).
			max_attempts: Retry attempts per model.

		Returns:
			The dspy.Prediction produced by the module.

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
		adapter: Any | None = None,
	) -> dict[str, Any]:
		"""Run memory-write sync flow and return stable contract payload.

		Runs the PydanticAI three-pass extraction pipeline
		(`run_extraction_three_pass`) with primary and fallback models.

		Args:
			trace_path: Path to the session trace JSONL file.
			memory_root: Override for the memory directory.
			workspace_root: Override for the workspace directory.
			adapter: Retained for backwards-compatible daemon signatures. No
				longer used — PydanticAI uses native function calling directly.

		Returns:
			Validated SyncResultContract payload dict.

		Raises:
			FileNotFoundError: If trace_path does not exist.
			RuntimeError: On agent failure or missing artifacts.
		"""
		del adapter  # DSPy adapter slot kept for caller compat; ignored
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

		# Build ONE robust PydanticAI model from Lerim Config. This is a
		# `FallbackModel` wrapping the primary provider/model with HTTP retry
		# (AsyncTenacityTransport for 429/5xx/network) plus every entry in
		# `[roles.agent].fallback_models` as a secondary model (also with its
		# own HTTP retry). The FallbackModel switches providers inside the
		# model layer — the enclosing agent run continues from where it was
		# without any restart.
		#
		# The outer `_run_with_fallback` loop is kept as a last-resort safety
		# net: if something escapes both the HTTP retry and FallbackModel
		# layers (e.g., UsageLimitExceeded from PydanticAI, or a connector
		# bug), it handles the retry/logging/short-circuit. In the common
		# case it sees no errors because tenacity + FallbackModel already
		# recovered them.
		def _primary_builder() -> Any:
			"""Return the (single) robust PydanticAI model for this run."""
			return build_pydantic_model("agent", config=self.config)

		def _call(model: Any) -> FinalizeResult:
			"""Invoke the PydanticAI three-pass pipeline with the given model.

			Per-pass usage limits flow from `default.toml` → `self.config.agent_role`
			→ here, so ops can tune them by editing `~/.lerim/config.toml` without
			touching source code.
			"""
			return run_extraction_three_pass(
				memory_root=resolved_memory_root,
				trace_path=trace_file,
				model=model,
				reflect_limit=self.config.agent_role.usage_limit_reflect,
				extract_limit=self.config.agent_role.usage_limit_extract,
				finalize_limit=self.config.agent_role.usage_limit_finalize,
				run_folder=run_folder,
				return_messages=False,
			)

		result: FinalizeResult = self._run_with_fallback(
			flow="sync",
			callable_fn=_call,
			model_builders=[_primary_builder],
		)

		# Extract completion summary from FinalizeResult.
		response_text = (result.completion_summary or "").strip() or "(no response)"

		# Write agent response text.
		_write_text_with_newline(artifact_paths["agent_log"], response_text)

		# The PydanticAI three-pass pipeline currently does not write
		# agent_trace.json itself. Write an empty placeholder so downstream
		# consumers (daemon, dashboard) don't break on missing file. Full
		# message capture is deferred until Phase 3 wires return_messages=True.
		agent_trace_path = run_folder / "agent_trace.json"
		if not agent_trace_path.exists():
			agent_trace_path.write_text("[]", encoding="utf-8")

		payload = {
			"trace_path": str(trace_file),
			"memory_root": str(resolved_memory_root),
			"workspace_root": str(resolved_workspace_root),
			"run_folder": str(run_folder),
			"artifacts": {
				key: str(path) for key, path in artifact_paths.items()
			},
			"cost_usd": 0.0,
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
		prediction = self._run_dspy_with_fallback(
			flow="maintain",
			module=agent,
			input_args={},
		)

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
			"cost_usd": 0.0,
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
			(response_text, session_id, cost_usd) tuple.  cost_usd is
			always 0.0 (cost tracking removed; field kept for API compat).
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
		prediction = self._run_dspy_with_fallback(
			flow="ask",
			module=agent,
			input_args={
				"question": prompt,
				"hints": hints,
			},
		)

		response_text = str(
			getattr(prediction, "answer", "") or ""
		).strip() or "(no response)"
		return response_text, resolved_session_id, 0.0
