"""Runtime orchestrator for Lerim sync, maintain, and ask (PydanticAI only).

All three flows run through PydanticAI models and shared retry/fallback logic.
"""

from __future__ import annotations

import json
import logging
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from lerim.agents.ask import format_ask_hints, run_ask
from lerim.agents.contracts import MaintainResultContract, SyncResultContract
from lerim.agents.extract import ExtractionResult, run_extraction
from lerim.agents.maintain import run_maintain
from lerim.config.providers import build_pydantic_model
from lerim.config.settings import Config, get_config
from lerim.memory.repo import build_memory_paths, ensure_project_memory
from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter

logger = logging.getLogger("lerim.runtime")


# ---------------------------------------------------------------------------
# Path helpers
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
		else (config.global_data_dir / "workspace")
	)
	return resolved_memory_root, resolved_workspace_root


def _ensure_memory_root_layout(memory_root: Path) -> None:
	"""Ensure memory root has canonical folders + index file."""
	ensure_project_memory(build_memory_paths(memory_root.parent))
	index_path = memory_root / "index.md"
	if not index_path.exists():
		index_path.write_text("# Memory Index\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Artifact I/O
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


def _write_agent_trace(path: Path, messages: list[ModelMessage]) -> None:
	"""Serialize PydanticAI message history to a stable JSON artifact."""
	trace_data = ModelMessagesTypeAdapter.dump_python(messages, mode="json")
	path.write_text(json.dumps(trace_data, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Quota error detection (PydanticAI path)
# ---------------------------------------------------------------------------


def _is_quota_error_pydantic(exc: Exception) -> bool:
	"""Detect rate-limit / quota errors across PydanticAI provider backends."""
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

	msg = str(exc).lower()
	return "429" in msg or "rate limit" in msg or "quota" in msg


class LerimRuntime:
	"""Runtime orchestrator — PydanticAI sync, maintain, and ask."""

	def __init__(
		self,
		default_cwd: str | None = None,
		config: Config | None = None,
	) -> None:
		"""Create runtime with validated provider configuration."""
		cfg = config or get_config()
		self.config = cfg
		self._default_cwd = default_cwd

		from lerim.config.providers import validate_provider_for_role

		validate_provider_for_role(cfg.agent_role.provider, "agent")

	@staticmethod
	def generate_session_id() -> str:
		"""Generate a unique session ID for ask mode."""
		return f"lerim-{secrets.token_hex(6)}"

	def _run_with_fallback(
		self,
		*,
		flow: str,
		callable_fn: Callable[[Any], Any],
		model_builders: list[Callable[[], Any]],
		max_attempts: int = 3,
	) -> Any:
		"""Run a PydanticAI callable with retry + model-builder fallback support."""
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
					logger.warning(f"[{flow}] usage limit exceeded, short-circuiting: {exc}")
					raise
				except Exception as exc:
					last_exc = exc
					if _is_quota_error_pydantic(exc):
						logger.warning(f"[{flow}] quota error on {model_label}: {str(exc)[:100]}")
						break
					if attempt < max_attempts:
						wait_time = min(2 ** attempt, 8)
						logger.warning(
							f"[{flow}] transient error on attempt {attempt}/{max_attempts} "
							f"({type(exc).__name__}): {str(exc)[:100]}; retrying in {wait_time}s..."
						)
						time.sleep(wait_time)
						continue
					logger.error(f"[{flow}] exhausted retries on {model_label}: {str(exc)[:100]}")
					break

		raise RuntimeError(
			f"[{flow}] Failed after trying {len(model_builders)} model(s). "
			f"Last error: {last_exc}"
		) from last_exc

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
		"""Run memory-write sync flow and return stable contract payload."""
		del adapter  # retained for older call-sites; no longer used
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

		_ensure_memory_root_layout(resolved_memory_root)

		def _primary_builder() -> Any:
			return build_pydantic_model("agent", config=self.config)

		def _call(model: Any) -> ExtractionResult:
			return run_extraction(
				memory_root=resolved_memory_root,
				trace_path=trace_file,
				model=model,
				run_folder=run_folder,
				return_messages=False,
			)

		result: ExtractionResult = self._run_with_fallback(
			flow="sync",
			callable_fn=_call,
			model_builders=[_primary_builder],
		)

		response_text = (result.completion_summary or "").strip() or "(no response)"
		_write_text_with_newline(artifact_paths["agent_log"], response_text)

		agent_trace_path = run_folder / "agent_trace.json"
		if not agent_trace_path.exists():
			agent_trace_path.write_text("[]", encoding="utf-8")

		payload = {
			"trace_path": str(trace_file),
			"memory_root": str(resolved_memory_root),
			"workspace_root": str(resolved_workspace_root),
			"run_folder": str(run_folder),
			"artifacts": {key: str(path) for key, path in artifact_paths.items()},
			"cost_usd": 0.0,
		}
		return SyncResultContract.model_validate(payload).model_dump(mode="json")

	# ------------------------------------------------------------------
	# Maintain flow
	# ------------------------------------------------------------------

	def maintain(
		self,
		memory_root: str | Path | None = None,
		workspace_root: str | Path | None = None,
	) -> dict[str, Any]:
		"""Run memory maintenance flow and return stable contract payload."""
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

		_ensure_memory_root_layout(resolved_memory_root)

		def _primary_builder() -> Any:
			return build_pydantic_model("agent", config=self.config)

		def _call(model: Any) -> tuple[Any, list[ModelMessage]]:
			return run_maintain(
				memory_root=resolved_memory_root,
				model=model,
				request_limit=self.config.agent_role.max_iters_maintain,
				return_messages=True,
			)

		result, messages = self._run_with_fallback(
			flow="maintain",
			callable_fn=_call,
			model_builders=[_primary_builder],
		)

		response_text = (result.completion_summary or "").strip() or "(no response)"
		_write_text_with_newline(artifact_paths["agent_log"], response_text)

		agent_trace_path = run_folder / "agent_trace.json"
		try:
			_write_agent_trace(agent_trace_path, messages)
		except Exception as exc:
			logger.warning(f"[maintain] Failed to write agent trace: {exc}")
			agent_trace_path.write_text("[]", encoding="utf-8")

		index_path = resolved_memory_root / "index.md"
		if index_path.exists():
			logger.info(f"[maintain] Memory index at {index_path}")

		payload = {
			"memory_root": str(resolved_memory_root),
			"workspace_root": str(resolved_workspace_root),
			"run_folder": str(run_folder),
			"artifacts": {key: str(path) for key, path in artifact_paths.items()},
			"cost_usd": 0.0,
		}
		return MaintainResultContract.model_validate(payload).model_dump(mode="json")

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
		"""Run one ask prompt. Returns (response, session_id, cost_usd)."""
		del cwd
		resolved_memory_root = (
			Path(memory_root).expanduser().resolve() if memory_root else self.config.memory_dir
		)
		resolved_session_id = session_id or self.generate_session_id()
		hints = format_ask_hints(hits=[], context_docs=[])

		def _primary_builder() -> Any:
			return build_pydantic_model("agent", config=self.config)

		def _call(model: Any) -> Any:
			return run_ask(
				memory_root=resolved_memory_root,
				model=model,
				question=prompt,
				hints=hints,
				request_limit=self.config.agent_role.max_iters_ask,
				return_messages=False,
			)

		result = self._run_with_fallback(
			flow="ask",
			callable_fn=_call,
			model_builders=[_primary_builder],
		)
		response_text = (result.answer or "").strip() or "(no response)"
		return response_text, resolved_session_id, 0.0
