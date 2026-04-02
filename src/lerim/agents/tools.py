"""DSPy ReAct tools for lerim agents.

Plain functions. Each takes ctx as first arg. Use functools.partial(fn, ctx)
to bind ctx before passing to dspy.ReAct.

Memory types: user, feedback, project, reference.
All memories stored flat under memory_root.
"""

from __future__ import annotations

import json
import re
import shutil
from functools import partial
from pathlib import Path

from lerim.agents.context import RuntimeContext
from lerim.agents.contracts import is_within as _is_within
from lerim.agents.schemas import (
    MEMORY_TYPES,
    MemoryRecord,
    canonical_memory_filename,
    slugify,
    staleness_note,
)


def _allowed_roots(ctx: RuntimeContext) -> list[Path]:
    roots: list[Path] = []
    if ctx.memory_root:
        roots.append(ctx.memory_root)
    if ctx.run_folder:
        roots.append(ctx.run_folder)
    for extra in ctx.extra_read_roots or ():
        roots.append(extra)
    return roots


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


def read_file(ctx: RuntimeContext, file_path: str) -> str:
    """Read a memory file's full text content.

    For large session traces, use read_trace() or grep_trace() instead.

    Args:
        file_path: Absolute path to the file.
    """
    resolved = Path(file_path).resolve()
    roots = _allowed_roots(ctx)
    if not any(_is_within(resolved, r) for r in roots):
        return f"Error: path {file_path} is outside allowed roots"
    if not resolved.exists():
        return f"Error: file not found: {file_path}"
    if not resolved.is_file():
        return f"Error: not a file: {file_path}"
    return resolved.read_text(encoding="utf-8")


def read_trace(ctx: RuntimeContext, file_path: str, offset: int = 0, limit: int = 200) -> str:
    """Read a section of a session trace file (paginated).

    Traces can be thousands of lines. Use offset/limit to read chunks.

    Args:
        file_path: Absolute path to the trace file (.jsonl).
        offset: Line number to start from (0-based). Default 0.
        limit: Max lines to return. Default 200.
    """
    resolved = Path(file_path).resolve()
    roots = _allowed_roots(ctx)
    if not any(_is_within(resolved, r) for r in roots):
        return f"Error: path {file_path} is outside allowed roots"
    if not resolved.exists():
        return f"Error: file not found: {file_path}"
    lines = resolved.read_text(encoding="utf-8").splitlines()
    total = len(lines)
    chunk = lines[offset:offset + limit]
    return f"[trace: {total} lines total, showing {offset}-{offset + len(chunk)}]\n" + "\n".join(chunk)


def grep_trace(ctx: RuntimeContext, file_path: str, pattern: str, context_lines: int = 2) -> str:
    """Search a session trace for lines matching a pattern.

    Use this to find decisions, user statements, or errors in a large trace.

    Args:
        file_path: Absolute path to the trace file (.jsonl).
        pattern: Case-insensitive substring to search for.
        context_lines: Lines of context around each match. Default 2.
    """
    resolved = Path(file_path).resolve()
    roots = _allowed_roots(ctx)
    if not any(_is_within(resolved, r) for r in roots):
        return f"Error: path {file_path} is outside allowed roots"
    if not resolved.exists():
        return f"Error: file not found: {file_path}"
    lines = resolved.read_text(encoding="utf-8").splitlines()
    pat = re.compile(re.escape(pattern), re.IGNORECASE)
    matches = []
    for i, line in enumerate(lines):
        if pat.search(line):
            start = max(0, i - context_lines)
            end = min(len(lines), i + context_lines + 1)
            block = [f"{'>>>' if j == i else '   '} L{j}: {lines[j]}" for j in range(start, end)]
            matches.append("\n".join(block))
    if not matches:
        return f"No matches for '{pattern}' in {resolved.name} ({len(lines)} lines)"
    if len(matches) > 20:
        matches = matches[:20]
        matches.append("... (more matches truncated)")
    return f"[{len(matches)} matches for '{pattern}']\n\n" + "\n---\n".join(matches)


def scan_memory_manifest(ctx: RuntimeContext) -> str:
    """Scan all memory files and return a compact manifest.

    Returns JSON: {count, memories: [{name, description, type, filename, age}]}.
    """
    import frontmatter as fm_lib

    if not ctx.memory_root or not Path(ctx.memory_root).is_dir():
        return json.dumps({"error": "memory_root not set or missing"})
    root = Path(ctx.memory_root)
    manifest = []
    for md_file in sorted(root.glob("*.md")):
        if md_file.name == "MEMORY.md":
            continue
        try:
            post = fm_lib.load(str(md_file))
            manifest.append({
                "name": post.get("name", md_file.stem),
                "description": post.get("description", ""),
                "type": post.get("type", "project"),
                "filename": md_file.name,
                "age": staleness_note(post.get("created", "")),
            })
        except Exception:
            manifest.append({"name": md_file.stem, "filename": md_file.name, "type": "unknown", "description": "", "age": ""})
    return json.dumps({"count": len(manifest), "memories": manifest}, indent=2)


def write_memory(ctx: RuntimeContext, type: str, name: str, description: str, body: str) -> str:
    """Create a new memory file.

    Returns JSON: {"file_path": str, "bytes": int, "type": str}.

    Args:
        type: One of "user", "feedback", "project", "reference".
        name: Short title (max ~10 words).
        description: One-line hook for retrieval (~150 chars).
        body: Full content. For feedback/project: rule → **Why:** → **How to apply:**
    """
    if not ctx.memory_root:
        return "ERROR: memory_root is not set."
    if type not in MEMORY_TYPES:
        return f"ERROR: Invalid type='{type}'. Must be one of: {', '.join(MEMORY_TYPES)}."
    if not name or not name.strip():
        return "ERROR: name cannot be empty."
    if not description or not description.strip():
        return "ERROR: description cannot be empty."
    if not body or not body.strip():
        return "ERROR: body cannot be empty."
    try:
        record = MemoryRecord(
            type=type, name=name.strip(), description=description.strip(),
            body=body.strip(), id=slugify(name), source=ctx.run_id,
        )
    except Exception as exc:
        return f"ERROR: Invalid memory fields: {exc}"
    filename = canonical_memory_filename(title=name, run_id=ctx.run_id)
    target = ctx.memory_root / filename
    content = record.to_markdown()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return json.dumps({"file_path": str(target), "bytes": len(content.encode("utf-8")), "type": type})


def edit_memory(ctx: RuntimeContext, file_path: str, new_content: str) -> str:
    """Replace content of an existing memory file. Must start with ---.

    Args:
        file_path: Absolute path to the memory file.
        new_content: Complete replacement (frontmatter + body).
    """
    if not ctx.memory_root:
        return "ERROR: memory_root is not set."
    resolved = Path(file_path).resolve()
    if not _is_within(resolved, ctx.memory_root):
        return f"ERROR: path {file_path} is outside memory_root"
    if not resolved.exists():
        return f"ERROR: file not found: {file_path}"
    if not new_content.strip().startswith("---"):
        return "ERROR: new_content must start with YAML frontmatter (---)"
    resolved.write_text(new_content, encoding="utf-8")
    return json.dumps({"edited": True, "file_path": str(resolved), "bytes": len(new_content.encode("utf-8"))})


def archive_memory(ctx: RuntimeContext, file_path: str) -> str:
    """Soft-delete a memory by moving to archived/.

    Args:
        file_path: Absolute path to a .md memory file.
    """
    if not ctx.memory_root:
        return "ERROR: memory_root is not set."
    resolved = Path(file_path).resolve()
    if not _is_within(resolved, ctx.memory_root):
        return f"ERROR: path {file_path} is outside memory_root"
    if not resolved.exists():
        return f"ERROR: file not found: {file_path}"
    if resolved.suffix != ".md":
        return f"ERROR: only .md files can be archived. Got: {resolved.name}"
    target = ctx.memory_root / "archived" / resolved.name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(resolved), str(target))
    return json.dumps({"archived": True, "source": str(resolved), "target": str(target)})


def update_memory_index(ctx: RuntimeContext, content: str) -> str:
    """Write MEMORY.md index file. One line per memory, max 200 lines.

    Format: `- [Title](filename.md) — one-line description`

    Args:
        content: Full text content for MEMORY.md.
    """
    if not ctx.memory_root:
        return json.dumps({"error": "memory_root not set"})
    root = Path(ctx.memory_root)
    index_path = root / "MEMORY.md"
    lines = content.strip().splitlines()
    if len(lines) > 200:
        lines = lines[:200]
        lines.append("> WARNING: Truncated to 200 lines.")
    final = "\n".join(lines) + "\n"
    if len(final.encode("utf-8")) > 25_000:
        final = final.encode("utf-8")[:25_000].decode("utf-8", errors="ignore")
        final += "\n> WARNING: Truncated to 25KB.\n"
    index_path.write_text(final, encoding="utf-8")
    return json.dumps({"file_path": str(index_path), "bytes": len(final), "lines": len(final.splitlines())})


def write_summary(ctx: RuntimeContext, title: str, description: str, user_intent: str, session_narrative: str, tags: str = "") -> str:
    """Write a session summary to memory_root/summaries/.

    Args:
        title: Short session title (max 10 words).
        description: One-line description of what was achieved.
        user_intent: The user's goal (max 150 words).
        session_narrative: What happened chronologically (max 200 words).
        tags: Comma-separated topic tags.
    """
    import frontmatter as fm_lib
    from datetime import datetime, timezone

    if not ctx.memory_root:
        return "ERROR: memory_root is not set."
    if not title or not title.strip():
        return "ERROR: title cannot be empty."
    if not user_intent or not user_intent.strip():
        return "ERROR: user_intent cannot be empty."
    if not session_narrative or not session_narrative.strip():
        return "ERROR: session_narrative cannot be empty."

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    slug = slugify(title)
    date_compact = datetime.now(timezone.utc).strftime("%Y%m%d")
    time_compact = datetime.now(timezone.utc).strftime("%H%M%S")
    summaries_dir = ctx.memory_root / "summaries" / date_compact / time_compact
    summaries_dir.mkdir(parents=True, exist_ok=True)
    summary_path = summaries_dir / f"{slug}.md"

    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
    fm_dict = {"id": slug, "title": title.strip(), "created": now_iso, "source": ctx.run_id, "description": (description or "").strip(), "tags": tag_list}
    body = f"## User Intent\n\n{user_intent.strip()}\n\n## What Happened\n\n{session_narrative.strip()}"
    post = fm_lib.Post(body, **fm_dict)
    summary_path.write_text(fm_lib.dumps(post) + "\n", encoding="utf-8")

    if ctx.artifact_paths and "summary" in ctx.artifact_paths:
        artifact = ctx.artifact_paths["summary"]
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text(json.dumps({"summary_path": str(summary_path)}, indent=2) + "\n", encoding="utf-8")

    return json.dumps({"summary_path": str(summary_path), "bytes": len(summary_path.read_text(encoding="utf-8").encode("utf-8"))})


def list_files(ctx: RuntimeContext, directory: str, pattern: str = "*.md") -> str:
    """List file paths matching a glob pattern.

    Args:
        directory: Absolute path to search in.
        pattern: Glob pattern. Default "*.md".
    """
    resolved = Path(directory).resolve()
    roots = _allowed_roots(ctx)
    if not any(_is_within(resolved, r) for r in roots):
        return f"Error: directory {directory} is outside allowed roots"
    if not resolved.exists():
        return "[]"
    if not resolved.is_dir():
        return f"Error: not a directory: {directory}"
    return json.dumps(sorted(str(f) for f in resolved.glob(pattern)))


def write_report(ctx: RuntimeContext, file_path: str, content: str) -> str:
    """Write a JSON report to the run workspace.

    Args:
        file_path: Absolute path within run_folder.
        content: Valid JSON string.
    """
    resolved = Path(file_path).resolve()
    if not ctx.run_folder:
        return "Error: run_folder is not set"
    if not _is_within(resolved, ctx.run_folder):
        return f"Error: path {file_path} is outside workspace {ctx.run_folder}"
    try:
        json.loads(content)
    except json.JSONDecodeError:
        return "Error: content is not valid JSON"
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(content, encoding="utf-8")
    return f"Report written to {file_path}"


# ---------------------------------------------------------------------------
# Tool lists — partial-bind ctx, pass to dspy.ReAct
# ---------------------------------------------------------------------------


def _bind(fn, ctx):
    """Bind ctx to a tool function. Preserves __name__ and __doc__ for DSPy."""
    p = partial(fn, ctx)
    p.__name__ = fn.__name__
    p.__doc__ = fn.__doc__
    return p


def make_extract_tools(ctx: RuntimeContext) -> list:
    """Tools for ExtractAgent."""
    return [_bind(fn, ctx) for fn in [
        read_file, read_trace, grep_trace, scan_memory_manifest,
        write_memory, edit_memory, archive_memory,
        update_memory_index, write_summary, list_files, write_report,
    ]]


def make_maintain_tools(ctx: RuntimeContext) -> list:
    """Tools for MaintainAgent."""
    return [_bind(fn, ctx) for fn in [
        read_file, scan_memory_manifest, write_memory, edit_memory,
        archive_memory, update_memory_index, list_files, write_report,
    ]]


def make_ask_tools(ctx: RuntimeContext) -> list:
    """Tools for AskAgent."""
    return [_bind(fn, ctx) for fn in [
        read_file, scan_memory_manifest, list_files,
    ]]
