"""Runtime tool implementations for lead agents and read-only subagents."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Literal, Sequence, cast

from pydantic_ai import ModelRetry

from lerim.config.settings import Config, get_config
from lerim.memory.access_tracker import (
    extract_memory_id,
    init_access_db,
    is_body_read,
    record_access,
)
from lerim.memory.extract_pipeline import extract_memories_from_session_file
from lerim.memory.memory_record import (
    MEMORY_TYPE_FOLDERS,
    MemoryRecord,
    MemoryType,
    canonical_memory_filename,
    slugify,
)
from lerim.memory.summarization_pipeline import (
    summarize_trace_from_session_file,
    write_summary_markdown,
)


@dataclass(frozen=True)
class RuntimeToolContext:
    """Per-run context used by runtime tools for boundaries and tracking."""

    config: Config
    repo_root: Path
    memory_root: Path | None
    workspace_root: Path | None
    run_folder: Path | None
    extra_read_roots: tuple[Path, ...]
    run_id: str
    trace_path: Path | None = None
    artifact_paths: dict[str, Path] | None = None


def _is_within(path: Path, root: Path) -> bool:
    """Return whether path is equal to or inside root."""
    resolved = path.resolve()
    root_resolved = root.resolve()
    return resolved == root_resolved or root_resolved in resolved.parents


def _resolve_path(path: str, cwd: Path) -> Path:
    """Resolve an absolute path from a potentially relative path string."""
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = cwd / candidate
    return candidate.resolve()


def build_tool_context(
    *,
    repo_root: str | Path,
    memory_root: str | Path | None = None,
    workspace_root: str | Path | None = None,
    run_folder: str | Path | None = None,
    extra_read_roots: Sequence[str | Path] | None = None,
    run_id: str = "",
    config: Config | None = None,
    trace_path: str | Path | None = None,
    artifact_paths: dict[str, Path] | None = None,
) -> RuntimeToolContext:
    """Build canonical runtime tool context for one agent run."""
    cfg = config or get_config()
    return RuntimeToolContext(
        config=cfg,
        repo_root=Path(repo_root).expanduser().resolve(),
        memory_root=Path(memory_root).expanduser().resolve() if memory_root else None,
        workspace_root=(
            Path(workspace_root).expanduser().resolve() if workspace_root else None
        ),
        run_folder=Path(run_folder).expanduser().resolve() if run_folder else None,
        extra_read_roots=tuple(
            Path(path).expanduser().resolve() for path in (extra_read_roots or [])
        ),
        run_id=str(run_id or ""),
        trace_path=Path(trace_path).expanduser().resolve() if trace_path else None,
        artifact_paths=artifact_paths,
    )


def _default_cwd(context: RuntimeToolContext) -> Path:
    """Return memory_root as default working directory for path resolution."""
    if context.memory_root:
        return context.memory_root
    raise RuntimeError("no_cwd_available: memory_root is None")


def _global_cache_dir() -> Path:
    """Return the global cache directory where adapters export traces."""
    return Path("~/.lerim/cache").expanduser().resolve()


def _read_allowed_roots(context: RuntimeToolContext) -> tuple[Path, ...]:
    """Return allowed read roots for read/glob/grep tools."""
    roots: list[Path] = []
    if context.memory_root:
        roots.append(context.memory_root)
    if context.workspace_root:
        roots.append(context.workspace_root)
    if context.run_folder:
        roots.append(context.run_folder)
    roots.append(_global_cache_dir())
    roots.extend(context.extra_read_roots)
    return tuple(dict.fromkeys(roots))


def _check_read_boundary(path: Path, context: RuntimeToolContext) -> str | None:
    """Return error string when read target is outside approved roots, else None."""
    roots = _read_allowed_roots(context)
    if not roots or not any(_is_within(path, root) for root in roots):
        return (
            f"ERROR: Cannot read '{path}': outside allowed roots. "
            f"Readable paths: {', '.join(str(r) for r in roots)}"
        )
    return None


def _write_allowed_roots(context: RuntimeToolContext) -> tuple[Path, ...]:
    """Return allowed write roots for write/edit tools."""
    roots: list[Path] = []
    if context.memory_root:
        roots.append(context.memory_root)
    if context.run_folder:
        roots.append(context.run_folder)
    return tuple(dict.fromkeys(roots))


def _assert_write_boundary(path: Path, context: RuntimeToolContext) -> None:
    """Raise when write target is outside memory/run-folder boundaries."""
    roots = _write_allowed_roots(context)
    if not roots or not any(_is_within(path, root) for root in roots):
        raise RuntimeError(
            f"Cannot write '{path}': outside allowed roots. "
            f"Writable paths: {', '.join(str(r) for r in roots)}"
        )


def _memory_primitive_type(path: Path, memory_root: Path | None) -> MemoryType | None:
    """Detect memory primitive type for a path inside memory root."""
    if not memory_root:
        return None
    for primitive, folder in MEMORY_TYPE_FOLDERS.items():
        folder_path = (memory_root / folder).resolve()
        if _is_within(path, folder_path):
            return primitive
    return None


def _record_memory_access(
    *,
    context: RuntimeToolContext,
    file_path: Path,
    limit: int | None,
    require_body_read: bool,
) -> None:
    """Record memory access event after boundary and visibility checks."""
    if not context.memory_root:
        return
    if not _is_within(file_path, context.memory_root):
        return
    if require_body_read and not is_body_read({"limit": limit}):
        return
    mem_id = extract_memory_id(str(file_path), str(context.memory_root))
    if not mem_id:
        return
    init_access_db(context.config.memories_db_path)
    record_access(context.config.memories_db_path, mem_id, str(context.memory_root))


def read_file_tool(
    *,
    context: RuntimeToolContext,
    file_path: str,
    offset: int = 1,
    limit: int = 2000,
) -> str:
    """Read file contents with line numbers and optional offset/limit window."""
    resolved = _resolve_path(file_path, _default_cwd(context))
    boundary_err = _check_read_boundary(resolved, context)
    if boundary_err:
        return boundary_err
    if not resolved.exists():
        return f"ERROR: File not found: '{resolved}'. Use glob to discover files."
    if resolved.is_dir():
        entries = sorted(
            [p.name + ("/" if p.is_dir() else "") for p in resolved.iterdir()]
        )
        return "\n".join(entries)

    text = resolved.read_text(encoding="utf-8")
    lines = text.splitlines()
    start = max(offset, 1)
    end = start + max(limit, 1) - 1
    numbered = [
        f"{idx}: {line}"
        for idx, line in enumerate(lines, start=1)
        if start <= idx <= end
    ]
    _record_memory_access(
        context=context, file_path=resolved, limit=limit, require_body_read=True
    )
    return "\n".join(numbered)


def glob_files_tool(
    *,
    context: RuntimeToolContext,
    pattern: str,
    base_path: str | None = None,
) -> list[str]:
    """Return sorted file matches for a glob pattern relative to base_path."""
    cwd = _default_cwd(context)
    base = _resolve_path(base_path or str(cwd), cwd)
    if _check_read_boundary(base, context):
        return []
    if not base.exists() or not base.is_dir():
        return []
    read_roots = _read_allowed_roots(context)
    candidates = [p.resolve() for p in base.glob(pattern)]
    return sorted(
        str(p) for p in candidates if any(_is_within(p, r) for r in read_roots)
    )


def grep_files_tool(
    *,
    context: RuntimeToolContext,
    pattern: str,
    base_path: str | None = None,
    include: str = "*.md",
    max_hits: int = 200,
) -> list[str]:
    """Search files by regex via ripgrep and return ``path:line:content`` hits."""
    import subprocess

    cwd = _default_cwd(context)
    base = _resolve_path(base_path or str(cwd), cwd)
    if _check_read_boundary(base, context):
        return []
    if not base.exists() or not base.is_dir():
        return []

    cmd = [
        "rg",
        "--no-heading",
        "--line-number",
        "--color=never",
        f"--max-count={max_hits}",
        f"--glob={include}",
        pattern,
        str(base),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    # rg exit 1 = no matches, exit 2 = error
    if result.returncode == 2:
        raise RuntimeError(f"grep_failed: {result.stderr.strip()}")
    if not result.stdout:
        return []

    read_roots = _read_allowed_roots(context)
    hits: list[str] = []
    for line in result.stdout.splitlines():
        # rg output: /path/to/file:line_number:content
        colon1 = line.find(":")
        if colon1 < 0:
            continue
        file_part = line[:colon1]
        try:
            resolved = Path(file_part).resolve()
        except (OSError, ValueError):
            continue
        if not any(_is_within(resolved, root) for root in read_roots):
            continue
        hits.append(line)
        if len(hits) >= max_hits:
            break
    return hits


def write_file_tool(
    *,
    context: RuntimeToolContext,
    file_path: str,
    content: str,
) -> dict[str, Any]:
    """Write file content under guarded roots. Memory primitives are rejected — use write_memory_tool instead."""
    resolved = _resolve_path(file_path, _default_cwd(context))
    try:
        _assert_write_boundary(resolved, context)
    except RuntimeError as exc:
        raise ModelRetry(str(exc)) from exc
    primitive_type = _memory_primitive_type(resolved, context.memory_root)
    if primitive_type is not None and primitive_type != MemoryType.summary:
        raise ModelRetry(
            "Use write_memory tool for memory files. "
            "It accepts structured fields (primitive, title, body, confidence, tags, kind) "
            "and builds the markdown automatically."
        )
    if primitive_type == MemoryType.summary:
        raise ModelRetry(
            "Cannot write summaries directly. Use summarize_pipeline tool."
        )

    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(content, encoding="utf-8")
    _record_memory_access(
        context=context, file_path=resolved, limit=None, require_body_read=False
    )
    return {
        "file_path": str(resolved),
        "bytes": len(content.encode("utf-8")),
        "primitive": None,
    }


def write_memory_tool(
    *,
    context: RuntimeToolContext,
    primitive: str,
    title: str,
    body: str,
    confidence: float = 0.8,
    tags: list[str] | None = None,
    kind: str | None = None,
) -> dict[str, Any]:
    """Write a structured memory record to the canonical memory folder.

    Python builds the markdown from structured fields — the LLM never touches
    frontmatter format directly. Raises ModelRetry on validation failure so
    the LLM can self-correct.
    """
    if not context.memory_root:
        raise RuntimeError("memory_root is not set")
    if primitive not in ("decision", "learning"):
        raise ModelRetry(
            f"Invalid primitive='{primitive}'. Must be exactly 'decision' or 'learning' (singular, lowercase). "
            "Example: write_memory(primitive='decision', title='...', body='...', confidence=0.8)"
        )
    if not title or not title.strip():
        raise ModelRetry(
            "title cannot be empty. Provide a short descriptive title. "
            "Example: 'Use SQLite for session indexing'"
        )
    if not (0.0 <= confidence <= 1.0):
        raise ModelRetry(
            f"confidence={confidence} out of range. Must be 0.0-1.0. Use 0.8 as default."
        )
    VALID_KINDS = {"insight", "procedure", "friction", "pitfall", "preference"}
    if primitive == "learning" and (not kind or kind not in VALID_KINDS):
        raise ModelRetry(
            f"Learning memories require 'kind'. Got kind={kind!r}. "
            f"Must be one of: {', '.join(sorted(VALID_KINDS))}. "
            "Example: write_memory(primitive='learning', title='...', body='...', kind='insight')"
        )
    try:
        record = MemoryRecord(
            primitive=cast(Literal["decision", "learning"], primitive),
            title=title,
            body=body,
            confidence=confidence,
            tags=tags or [],
            kind=kind,
            id=slugify(title),
            source=context.run_id,
        )
    except Exception as exc:
        raise ModelRetry(
            f"Invalid memory fields: {exc}. "
            "Required: primitive ('decision'|'learning'), title (non-empty string), body (non-empty string). "
            "Optional: confidence (0.0-1.0, default 0.8), tags (list of strings), kind (required for learnings)."
        ) from exc

    mem_type = MemoryType(record.primitive)
    folder = MEMORY_TYPE_FOLDERS[mem_type]
    filename = canonical_memory_filename(title=title, run_id=context.run_id)
    target = context.memory_root / folder / filename

    content = record.to_markdown()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    _record_memory_access(
        context=context, file_path=target, limit=None, require_body_read=False
    )
    return {
        "file_path": str(target),
        "bytes": len(content.encode("utf-8")),
        "primitive": mem_type.value,
    }


def edit_file_tool(
    *,
    context: RuntimeToolContext,
    file_path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
) -> dict[str, Any]:
    """Edit file text under guarded roots with deterministic replacement semantics."""
    resolved = _resolve_path(file_path, _default_cwd(context))
    _assert_write_boundary(resolved, context)
    if not resolved.exists() or not resolved.is_file():
        raise FileNotFoundError(
            f"Cannot edit '{resolved}': file not found. Use glob to discover files."
        )
    primitive_type = _memory_primitive_type(resolved, context.memory_root)
    if primitive_type == MemoryType.summary:
        raise RuntimeError("summary_write_reserved_for_pipeline")

    text = resolved.read_text(encoding="utf-8")
    if old_string not in text:
        raise RuntimeError(
            f"Cannot edit '{resolved}': old_string not found in file. "
            "Read the file first to get the exact text to replace."
        )

    if replace_all:
        updated = text.replace(old_string, new_string)
        replacements = text.count(old_string)
    else:
        updated = text.replace(old_string, new_string, 1)
        replacements = 1

    resolved.write_text(updated, encoding="utf-8")
    _record_memory_access(
        context=context, file_path=resolved, limit=None, require_body_read=False
    )
    return {
        "file_path": str(resolved),
        "replacements": replacements,
        "bytes": len(updated.encode("utf-8")),
    }


def run_extract_pipeline_tool(
    *,
    context: RuntimeToolContext,
    guidance: str | None = None,
) -> dict[str, Any]:
    """Run extraction pipeline and write JSON artifact output."""
    if not context.trace_path or not context.artifact_paths:
        raise RuntimeError("trace_path and artifact_paths required in context")
    output_file = context.artifact_paths["extract"]
    effective_guidance = str(guidance or "").strip()
    if not effective_guidance:
        effective_guidance = (
            "Focus on user decisions and preferences. "
            "Skip generic research findings, web search results, "
            "and code architecture facts derivable from reading the source."
        )
    candidates = extract_memories_from_session_file(
        context.trace_path,
        guidance=effective_guidance,
    )
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(
        json.dumps(candidates, ensure_ascii=True, indent=2) + "\n", encoding="utf-8"
    )
    return {
        "output_path": str(output_file),
        "candidate_count": len(candidates),
    }


def run_summarization_pipeline_tool(
    *,
    context: RuntimeToolContext,
    guidance: str | None = None,
) -> dict[str, Any]:
    """Run summarization pipeline and write summary pointer artifact."""
    if not context.memory_root:
        raise RuntimeError("memory_root_required_for_summary_pipeline")
    if not context.trace_path or not context.artifact_paths:
        raise RuntimeError("trace_path and artifact_paths required in context")
    output_file = context.artifact_paths["summary"]
    metadata = {
        "run_id": context.run_id,
        "trace_path": str(context.trace_path),
        "raw_trace_path": str(context.trace_path),
    }
    payload = summarize_trace_from_session_file(
        context.trace_path,
        metadata=metadata,
        guidance=str(guidance or "").strip(),
    )
    summary_path = write_summary_markdown(
        payload, context.memory_root, run_id=context.run_id
    )

    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(
        json.dumps({"summary_path": str(summary_path)}, ensure_ascii=True, indent=2)
        + "\n",
        encoding="utf-8",
    )
    return {
        "output_path": str(output_file),
        "summary_path": str(summary_path),
    }


if __name__ == "__main__":
    """Run real-path tool smoke checks for boundaries."""
    with TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        memory_root = root / "memory"
        workspace_root = root / "workspace"
        run_folder = workspace_root / "sync-20260223-000000-aaaaaa"
        (memory_root / "decisions").mkdir(parents=True)
        (memory_root / "learnings").mkdir(parents=True)
        run_folder.mkdir(parents=True)

        context = build_tool_context(
            repo_root=root,
            memory_root=memory_root,
            workspace_root=workspace_root,
            run_folder=run_folder,
            run_id=run_folder.name,
        )

        # write_memory_tool: structured memory write
        write_result = write_memory_tool(
            context=context,
            primitive="learning",
            title="Queue heartbeat",
            body="Keep heartbeat updates deterministic.",
            confidence=0.8,
            tags=["queue"],
            kind="insight",
        )
        assert write_result["file_path"].endswith("-queue-heartbeat.md")

        # write_file_tool: rejects memory primitive paths with ModelRetry
        try:
            write_file_tool(
                context=context,
                file_path=str(memory_root / "learnings" / "draft.md"),
                content="---\ntitle: test\n---\nbody",
            )
            raise AssertionError("expected ModelRetry for memory primitive path")
        except ModelRetry as exc:
            assert "write_memory" in str(exc)

        # write_file_tool: boundary denial for outside paths (now ModelRetry)
        try:
            write_file_tool(
                context=context,
                file_path=str(root / "outside.md"),
                content="outside",
            )
            raise AssertionError("expected write boundary denial")
        except ModelRetry as exc:
            assert "Cannot write" in str(exc) and "outside allowed roots" in str(exc)

    print("runtime tools: self-test passed")
