"""PydanticAI runtime for Lerim chat, sync, and maintain flows."""

from __future__ import annotations

import json
import logging
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import logfire
from pydantic import BaseModel
from pydantic_ai import Agent, RunContext

from lerim.config.settings import Config, LLMRoleConfig, get_config
from lerim.memory.access_tracker import get_access_stats, init_access_db
from lerim.runtime.contracts import MaintainCounts, SyncCounts
from lerim.runtime.prompts import build_maintain_prompt, build_sync_prompt
from lerim.runtime.prompts.maintain import build_maintain_artifact_paths
from lerim.runtime.prompts.system import build_lead_system_prompt
from lerim.runtime.providers import build_orchestration_model_from_role
from lerim.runtime.subagents import get_explorer_agent
from lerim.runtime.tools import (
    RuntimeToolContext,
    build_tool_context,
    edit_file_tool,
    glob_files_tool,
    grep_files_tool,
    read_file_tool,
    run_extract_pipeline_tool,
    run_summarization_pipeline_tool,
    write_file_tool,
)

logger = logging.getLogger("lerim.runtime")

CHAT_TOOLS = ["read", "grep", "glob", "explore"]
SYNC_TOOLS = [
    "read",
    "grep",
    "glob",
    "explore",
    "write",
    "extract_pipeline",
    "summarize_pipeline",
]
MAINTAIN_TOOLS = [
    "read",
    "grep",
    "glob",
    "explore",
    "write",
    "edit",
]


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


class MaintainResultContract(BaseModel):
    """Stable maintain return payload schema used by CLI and daemon."""

    memory_root: str
    workspace_root: str
    run_folder: str
    artifacts: dict[str, str]
    counts: MaintainCounts


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


class LerimAgent:
    """Lead runtime wrapper for chat, sync, and maintain orchestration."""

    def __init__(
        self,
        model: str | None = None,
        provider: str | None = None,
        timeout_seconds: int | None = None,
        single_tools: list[str] | None = None,
        allowed_read_dirs: list[str | Path] | None = None,
        default_cwd: str | None = None,
    ) -> None:
        """Create runtime with role-based model wiring and tool policy defaults."""
        config = get_config()
        lead_role = config.lead_role
        resolved_timeout = timeout_seconds or lead_role.timeout_seconds
        role = LLMRoleConfig(
            provider=(provider or lead_role.provider),
            model=(model or lead_role.model),
            api_base=lead_role.api_base,
            fallback_models=lead_role.fallback_models,
            timeout_seconds=max(1, int(resolved_timeout)),
            max_iterations=lead_role.max_iterations,
            openrouter_provider_order=lead_role.openrouter_provider_order,
        )
        self.config = config
        self.model = build_orchestration_model_from_role(role, config=config)
        self.system_prompt = build_lead_system_prompt()
        self.single_tools = (
            list(single_tools) if single_tools is not None else list(CHAT_TOOLS)
        )
        self._allowed_read_dirs = [
            Path(path).expanduser().resolve() for path in (allowed_read_dirs or [])
        ]
        self._default_cwd = default_cwd
        self._timeout_seconds = max(1, int(role.timeout_seconds))

    @staticmethod
    def generate_session_id() -> str:
        """Generate a random session identifier."""
        return f"lerim-{secrets.token_hex(6)}"

    @staticmethod
    def _is_within(path: Path, root: Path) -> bool:
        """Return whether path equals or is inside root."""
        resolved = path.resolve()
        root_resolved = root.resolve()
        return resolved == root_resolved or root_resolved in resolved.parents

    def _build_lead_agent(self, mode: str) -> Agent[RuntimeToolContext, str]:
        """Build one lead PydanticAI agent with mode-specific tool registration."""
        mode_tools = {
            "chat": CHAT_TOOLS,
            "sync": SYNC_TOOLS,
            "maintain": MAINTAIN_TOOLS,
        }
        allowed_tools = set(
            self.single_tools if mode == "chat" else mode_tools.get(mode, CHAT_TOOLS)
        )

        instructions = f"""\
{self.system_prompt}
Current mode: {mode}. \
Always use tools to read/write files and produce concise completion output."""
        retries = 2 if mode in {"sync", "maintain"} else 1
        agent = Agent[
            RuntimeToolContext,
            str,
        ](
            model=self.model,
            output_type=str,
            deps_type=RuntimeToolContext,
            name=f"lerim-{mode}",
            instructions=instructions,
            retries=retries,
            tool_timeout=float(self._timeout_seconds),
        )

        if "read" in allowed_tools:

            @agent.tool
            def read(
                ctx: RunContext[RuntimeToolContext],
                file_path: str,
                offset: int = 1,
                limit: int = 2000,
            ) -> str:
                """Read file content with line numbers."""
                return read_file_tool(
                    context=ctx.deps,
                    file_path=file_path,
                    offset=offset,
                    limit=limit,
                )

        if "glob" in allowed_tools:

            @agent.tool
            def glob(
                ctx: RunContext[RuntimeToolContext],
                pattern: str,
                base_path: str | None = None,
            ) -> list[str]:
                """Find files by glob pattern."""
                return glob_files_tool(
                    context=ctx.deps, pattern=pattern, base_path=base_path
                )

        if "grep" in allowed_tools:

            @agent.tool
            def grep(
                ctx: RunContext[RuntimeToolContext],
                pattern: str,
                base_path: str | None = None,
                include: str = "*.md",
                max_hits: int = 200,
            ) -> list[str]:
                """Search files with regular expressions."""
                return grep_files_tool(
                    context=ctx.deps,
                    pattern=pattern,
                    base_path=base_path,
                    include=include,
                    max_hits=max_hits,
                )

        if "explore" in allowed_tools:

            @agent.tool
            def explore(
                ctx: RunContext[RuntimeToolContext],
                query: str,
            ) -> dict[str, Any]:
                """Delegate read-only evidence gathering to explorer subagent."""
                result = get_explorer_agent().run_sync(
                    query, deps=ctx.deps, usage=ctx.usage
                )
                return result.output.model_dump()

        if "write" in allowed_tools:

            @agent.tool
            def write(
                ctx: RunContext[RuntimeToolContext],
                file_path: str,
                content: str,
            ) -> dict[str, Any]:
                """Write file content with boundary checks and normalization."""
                return write_file_tool(
                    context=ctx.deps,
                    file_path=file_path,
                    content=content,
                )

        if "edit" in allowed_tools:

            @agent.tool
            def edit(
                ctx: RunContext[RuntimeToolContext],
                file_path: str,
                old_string: str,
                new_string: str,
                replace_all: bool = False,
            ) -> dict[str, Any]:
                """Edit file content with boundary checks."""
                return edit_file_tool(
                    context=ctx.deps,
                    file_path=file_path,
                    old_string=old_string,
                    new_string=new_string,
                    replace_all=replace_all,
                )

        if "extract_pipeline" in allowed_tools:

            @agent.tool
            def extract_pipeline(
                ctx: RunContext[RuntimeToolContext],
                trace_path: str,
                output_path: str,
                metadata: Any | None = None,
                metrics: Any | None = None,
            ) -> dict[str, Any]:
                """Run DSPy extraction pipeline and write JSON artifact.

                metadata and metrics may be dicts or JSON strings.
                """
                return run_extract_pipeline_tool(
                    context=ctx.deps,
                    trace_path=trace_path,
                    output_path=output_path,
                    metadata=metadata,
                    metrics=metrics,
                )

        if "summarize_pipeline" in allowed_tools:

            @agent.tool
            def summarize_pipeline(
                ctx: RunContext[RuntimeToolContext],
                trace_path: str,
                output_path: str,
                metadata: Any | None = None,
                metrics: Any | None = None,
            ) -> dict[str, Any]:
                """Run DSPy summarization pipeline and write summary pointer artifact.

                metadata and metrics may be dicts or JSON strings.
                """
                return run_summarization_pipeline_tool(
                    context=ctx.deps,
                    trace_path=trace_path,
                    output_path=output_path,
                    metadata=metadata,
                    metrics=metrics,
                )

        return agent

    def _run_agent_once(
        self,
        *,
        prompt: str,
        mode: str,
        context: RuntimeToolContext,
    ) -> tuple[str, str]:
        """Run one lead-agent prompt and return response text plus run id."""
        agent = self._build_lead_agent(mode)
        role = self.config.lead_role
        fallbacks = ", ".join(role.fallback_models) if role.fallback_models else "none"
        max_attempts = 3
        last_error = None
        with logfire.span(
            "lerim {mode} run",
            mode=mode,
            provider=role.provider,
            model=role.model,
            fallback_models=fallbacks,
        ):
            for attempt in range(1, max_attempts + 1):
                try:
                    logger.info(
                        f"[{mode}] {role.provider}/{role.model} (fallback: {fallbacks}) attempt {attempt}/{max_attempts}"
                    )
                    result = agent.run_sync(prompt, deps=context)
                    text = str(result.output or "").strip() or "(no response)"
                    return text, str(result.run_id)
                except Exception as exc:
                    last_error = exc
                    error_type = type(exc).__name__
                    error_msg = str(exc)
                    if "429" in error_msg or "rate limit" in error_msg.lower():
                        logger.warning(
                            f"[{mode}] Rate limited on attempt {attempt}: {error_msg[:100]}"
                        )
                    elif "500" in error_msg or "503" in error_msg:
                        logger.warning(
                            f"[{mode}] Server error on attempt {attempt}: {error_msg[:100]}"
                        )
                    elif attempt < max_attempts:
                        logger.warning(
                            f"[{mode}] Error on attempt {attempt} ({error_type}): {error_msg[:100]}"
                        )
                    if attempt < max_attempts:
                        wait_time = min(2**attempt, 8)
                        logger.info(f"[{mode}] Retrying in {wait_time}s...")
                        time.sleep(wait_time)
            raise RuntimeError(
                f"[{mode}] Failed after {max_attempts} attempts. Last error: {last_error}"
            ) from last_error

    @staticmethod
    def _assert_sync_contract(payload: dict[str, Any]) -> dict[str, Any]:
        """Validate sync payload against stable public contract."""
        return SyncResultContract.model_validate(payload).model_dump(mode="json")

    @staticmethod
    def _assert_maintain_contract(payload: dict[str, Any]) -> dict[str, Any]:
        """Validate maintain payload against stable public contract."""
        return MaintainResultContract.model_validate(payload).model_dump(mode="json")

    def chat(
        self,
        prompt: str,
        session_id: str | None = None,
        cwd: str | None = None,
        memory_root: str | Path | None = None,
    ) -> tuple[str, str]:
        """Run one chat prompt via lead runtime agent."""
        runtime_cwd = (
            Path(cwd or self._default_cwd or str(Path.cwd())).expanduser().resolve()
        )
        resolved_memory_root = (
            Path(memory_root).expanduser().resolve()
            if memory_root
            else self.config.memory_dir
        )
        context = build_tool_context(
            repo_root=runtime_cwd,
            memory_root=resolved_memory_root,
            workspace_root=self.config.data_dir / "workspace",
            run_folder=None,
            extra_read_roots=self._allowed_read_dirs,
            run_id=session_id or self.generate_session_id(),
            config=self.config,
        )
        response, resolved_session = self._run_agent_once(
            prompt=prompt,
            mode="chat",
            context=context,
        )
        return response, (session_id or resolved_session)

    def sync(
        self,
        trace_path: str | Path,
        memory_root: str | Path | None = None,
        workspace_root: str | Path | None = None,
    ) -> dict[str, Any]:
        """Run memory-write sync flow and return stable contract payload."""
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

        prompt = build_sync_prompt(
            trace_file=trace_file,
            memory_root=resolved_memory_root,
            run_folder=run_folder,
            artifact_paths=artifact_paths,
            metadata=metadata,
        )
        extra_roots = list(self._allowed_read_dirs) + [trace_file.parent]
        context = build_tool_context(
            repo_root=repo_root,
            memory_root=resolved_memory_root,
            workspace_root=resolved_workspace_root,
            run_folder=run_folder,
            extra_read_roots=extra_roots,
            run_id=run_folder.name,
            config=self.config,
        )
        response, _ = self._run_agent_once(
            prompt=prompt,
            mode="sync",
            context=context,
        )
        _write_text_with_newline(artifact_paths["agent_log"], response)

        for key in ("extract", "summary", "memory_actions", "subagents_log"):
            if not artifact_paths[key].exists():
                raise RuntimeError(f"missing_artifact:{artifact_paths[key]}")

        try:
            summary_artifact = json.loads(
                artifact_paths["summary"].read_text(encoding="utf-8")
            )
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"invalid_json_artifact:{artifact_paths['summary']}"
            ) from exc
        raw_summary = str(
            (summary_artifact if isinstance(summary_artifact, dict) else {}).get(
                "summary_path", ""
            )
        ).strip()
        if not raw_summary:
            raise RuntimeError("missing_summary_path_in_pipeline_output")
        summary_path_resolved = Path(raw_summary).resolve()
        if not self._is_within(summary_path_resolved, resolved_memory_root):
            raise RuntimeError(
                f"summary_path_outside_memory_root:{summary_path_resolved}"
            )
        if not summary_path_resolved.exists():
            raise RuntimeError(f"summary_path_not_found:{summary_path_resolved}")

        report = _load_json_dict_artifact(artifact_paths["memory_actions"])
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
                raise RuntimeError(f"report_path_outside_allowed_roots:{resolved}")
            written_memory_paths.append(str(resolved))

        payload = {
            "trace_path": str(trace_file),
            "memory_root": str(resolved_memory_root),
            "workspace_root": str(resolved_workspace_root),
            "run_folder": str(run_folder),
            "artifacts": {key: str(path) for key, path in artifact_paths.items()},
            "counts": counts,
            "written_memory_paths": written_memory_paths,
            "summary_path": str(summary_path_resolved),
        }
        return self._assert_sync_contract(payload)

    def maintain(
        self,
        memory_root: str | Path | None = None,
        workspace_root: str | Path | None = None,
    ) -> dict[str, Any]:
        """Run memory maintenance flow and return stable contract payload."""
        repo_root = Path(self._default_cwd or Path.cwd()).expanduser().resolve()
        resolved_memory_root, resolved_workspace_root = _resolve_runtime_roots(
            config=self.config,
            memory_root=memory_root,
            workspace_root=workspace_root,
        )
        run_folder = resolved_workspace_root / _default_run_folder_name("maintain")
        run_folder.mkdir(parents=True, exist_ok=True)
        artifact_paths = build_maintain_artifact_paths(run_folder)

        init_access_db(self.config.memories_db_path)
        access_stats = get_access_stats(
            self.config.memories_db_path,
            str(resolved_memory_root),
        )
        prompt = build_maintain_prompt(
            memory_root=resolved_memory_root,
            run_folder=run_folder,
            artifact_paths=artifact_paths,
            access_stats=access_stats,
            decay_days=self.config.decay_days,
            decay_archive_threshold=self.config.decay_archive_threshold,
            decay_min_confidence_floor=self.config.decay_min_confidence_floor,
            decay_recent_access_grace_days=self.config.decay_recent_access_grace_days,
        )
        context = build_tool_context(
            repo_root=repo_root,
            memory_root=resolved_memory_root,
            workspace_root=resolved_workspace_root,
            run_folder=run_folder,
            extra_read_roots=self._allowed_read_dirs,
            run_id=run_folder.name,
            config=self.config,
        )
        response, _ = self._run_agent_once(
            prompt=prompt,
            mode="maintain",
            context=context,
        )
        _write_text_with_newline(artifact_paths["agent_log"], response)

        actions_path = artifact_paths["maintain_actions"]
        if not actions_path.exists():
            raise RuntimeError(f"missing_artifact:{actions_path}")
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

        for action in report.get("actions") or []:
            if not isinstance(action, dict):
                continue
            for path_key in ("source_path", "target_path"):
                raw = str(action.get(path_key) or "").strip()
                if not raw:
                    continue
                resolved = Path(raw).resolve()
                if not (
                    self._is_within(resolved, resolved_memory_root)
                    or self._is_within(resolved, run_folder)
                ):
                    raise RuntimeError(
                        f"maintain_action_path_outside_allowed_roots:{path_key}={resolved}"
                    )

        payload = {
            "memory_root": str(resolved_memory_root),
            "workspace_root": str(resolved_workspace_root),
            "run_folder": str(run_folder),
            "artifacts": {key: str(path) for key, path in artifact_paths.items()},
            "counts": counts,
        }
        return self._assert_maintain_contract(payload)


if __name__ == "__main__":
    """Run runtime self-tests for prompt/builders and schema contracts."""
    agent = LerimAgent()
    assert agent.generate_session_id().startswith("lerim-")
    assert "lead runtime orchestrator" in agent.system_prompt

    with TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        trace_file = root / "trace.jsonl"
        trace_file.write_text('{"role":"user","content":"hello"}\n', encoding="utf-8")
        run_folder = root / "workspace" / "sync-selftest"
        prompt = build_sync_prompt(
            trace_file=trace_file,
            memory_root=root / "memory",
            run_folder=run_folder,
            artifact_paths=_build_artifact_paths(run_folder),
            metadata={
                "run_id": "sync-selftest",
                "trace_path": str(trace_file),
                "repo_name": "lerim",
            },
        )
        assert "artifact_paths_json" in prompt
        assert "extract_pipeline" in prompt

    sync_payload = {
        "trace_path": "/tmp/trace.jsonl",
        "memory_root": "/tmp/memory",
        "workspace_root": "/tmp/workspace",
        "run_folder": "/tmp/workspace/sync-1",
        "artifacts": {},
        "counts": {"add": 0, "update": 0, "no_op": 0},
        "written_memory_paths": [],
        "summary_path": "/tmp/memory/summaries/20260223/000000/test.md",
    }
    LerimAgent._assert_sync_contract(sync_payload)

    maintain_payload = {
        "memory_root": "/tmp/memory",
        "workspace_root": "/tmp/workspace",
        "run_folder": "/tmp/workspace/maintain-1",
        "artifacts": {},
        "counts": {
            "merged": 0,
            "archived": 0,
            "consolidated": 0,
            "decayed": 0,
            "unchanged": 0,
        },
    }
    LerimAgent._assert_maintain_contract(maintain_payload)
    print("runtime agent: self-test passed")
