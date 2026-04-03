"""Claude desktop session adapter for reading JSONL trace sessions."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from lerim.adapters.base import SessionRecord, ViewerMessage, ViewerSession
from lerim.adapters.common import (
    compact_jsonl,
    count_non_empty_files,
    in_window,
    load_jsonl_dict_lines,
    normalize_timestamp_iso,
    parse_timestamp,
    write_session_cache,
)


_DROP_TYPES = {"progress", "file-history-snapshot", "queue-operation", "pr-link"}
_KEEP_FIELDS = {"type", "message", "timestamp"}
_CANONICAL_TYPES = {"user", "assistant"}


def _clean_entry(obj: dict[str, Any]) -> dict[str, Any] | None:
    """Apply Claude-specific cleaning to a single JSONL entry.

    Drops: progress, file-history-snapshot, queue-operation, pr-link lines.
    Strips: metadata fields not needed for extraction (parentUuid, toolUseResult, etc.).
    Clears: all tool_result content (replaced with size descriptor).
    Clears: thinking block content (replaced with size descriptor).
    """
    if obj.get("type") in _DROP_TYPES:
        return None
    # Drop non-conversation types (system entries contain prompts, not conversation)
    if obj.get("type") not in _CANONICAL_TYPES:
        return None
    # Strip to only conversation-relevant fields
    obj = {k: v for k, v in obj.items() if k in _KEEP_FIELDS}
    # Normalize timestamp to ISO 8601 UTC
    obj["timestamp"] = normalize_timestamp_iso(obj.get("timestamp"))
    # Strip metadata from inner message -- keep only role and content
    msg = obj.get("message")
    if isinstance(msg, dict):
        obj["message"] = {k: v for k, v in msg.items() if k in {"role", "content"}}
        msg = obj["message"]
    # Clear tool_result content and thinking blocks
    if isinstance(msg, dict):
        content = msg.get("content")
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_result":
                    inner = block.get("content", "")
                    if isinstance(inner, str) and not inner.startswith("[cleared:"):
                        block["content"] = f"[cleared: {len(inner)} chars]"
                    elif isinstance(inner, list):
                        total = sum(
                            len(s.get("text", ""))
                            for s in inner
                            if isinstance(s, dict)
                        )
                        block["content"] = f"[cleared: {total} chars]"
                elif block.get("type") == "thinking":
                    text = block.get("thinking", "")
                    if not text.startswith("[thinking cleared:"):
                        block["thinking"] = f"[thinking cleared: {len(text)} chars]"
                    block.pop("signature", None)
    return obj


def compact_trace(raw_text: str) -> str:
    """Strip tool outputs and noise from Claude session JSONL."""
    return compact_jsonl(raw_text, _clean_entry)


def _default_cache_dir() -> Path:
    """Return the default cache directory for compacted Claude JSONL files."""
    return Path("~/.lerim/cache/claude").expanduser()


def default_path() -> Path | None:
    """Return the default Claude traces directory."""
    return Path("~/.claude/projects/").expanduser()


def count_sessions(path: Path) -> int:
    """Count readable non-empty Claude session JSONL files."""
    return count_non_empty_files(path, "*.jsonl")


def find_session_path(session_id: str, traces_dir: Path | None = None) -> Path | None:
    """Find a Claude session JSONL path by its stem-based session ID."""
    base = traces_dir or default_path()
    if base is None or not base.exists():
        return None
    for path in base.rglob("*.jsonl"):
        if path.stem == session_id:
            return path
    return None


def read_session(
    session_path: Path, session_id: str | None = None
) -> ViewerSession | None:
    """Parse one Claude session JSONL file into normalized viewer messages."""
    messages: list[ViewerMessage] = []
    tool_results: dict[str, Any] = {}
    tool_messages: dict[str, ViewerMessage] = {}
    resolved_session_id = session_id or session_path.stem
    git_branch = None
    total_input = 0
    total_output = 0
    cwd = None

    for entry in load_jsonl_dict_lines(session_path):
        entry_type = entry.get("type")
        timestamp = entry.get("timestamp")

        if not git_branch:
            git_branch = entry.get("gitBranch")
        if not cwd:
            cwd = entry.get("cwd")

        if entry_type == "user":
            content = entry.get("message", {}).get("content", "")
            if isinstance(content, list):
                text_parts: list[str] = []
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "tool_result":
                        tool_id = str(block.get("tool_use_id") or "")
                        result_content = block.get("content", "")
                        if isinstance(result_content, list):
                            result_content = "\n".join(
                                str(item.get("text") or "")
                                for item in result_content
                                if isinstance(item, dict)
                            )
                        tool_results[tool_id] = str(result_content)
                        if tool_id in tool_messages:
                            tool_messages[tool_id].tool_output = tool_results[tool_id]
                        else:
                            messages.append(
                                ViewerMessage(
                                    role="tool",
                                    tool_name="tool",
                                    tool_output=tool_results[tool_id],
                                    timestamp=timestamp,
                                )
                            )
                    elif block.get("type") == "text":
                        text_parts.append(str(block.get("text") or ""))
                content = "\n".join(text_parts)
            if (
                isinstance(content, str)
                and content.strip()
                and not content.startswith("<")
            ):
                messages.append(
                    ViewerMessage(role="user", content=content, timestamp=timestamp)
                )

        elif entry_type == "assistant":
            msg_data = entry.get("message", {})
            content_blocks = msg_data.get("content", [])
            model = msg_data.get("model")
            usage = msg_data.get("usage", {})
            if isinstance(usage, dict):
                total_input += int(usage.get("input_tokens", 0) or 0)
                total_output += int(usage.get("output_tokens", 0) or 0)

            text_parts: list[str] = []
            if isinstance(content_blocks, list):
                for block in content_blocks:
                    if not isinstance(block, dict):
                        continue
                    block_type = block.get("type")
                    if block_type == "text":
                        text_parts.append(str(block.get("text") or ""))
                    elif block_type == "tool_use":
                        tool_id = str(block.get("id") or "")
                        tool_name = str(block.get("name") or "")
                        tool_input = block.get("input", {})
                        tool_msg = ViewerMessage(
                            role="tool",
                            tool_name=tool_name,
                            tool_input=tool_input,
                            tool_output=tool_results.get(tool_id),
                            timestamp=timestamp,
                        )
                        tool_messages[tool_id] = tool_msg
                        messages.append(tool_msg)
            text = "\n".join(text_parts)
            if text or model:
                messages.append(
                    ViewerMessage(
                        role="assistant",
                        content=text,
                        timestamp=timestamp,
                        model=str(model) if model else None,
                    )
                )

    return ViewerSession(
        session_id=resolved_session_id,
        cwd=cwd,
        git_branch=git_branch,
        messages=messages,
        total_input_tokens=total_input,
        total_output_tokens=total_output,
    )


def iter_sessions(
    traces_dir: Path | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    known_run_ids: set[str] | None = None,
) -> list[SessionRecord]:
    """Enumerate Claude sessions, skipping those already indexed by ID."""
    base = traces_dir or default_path()
    if base is None or not base.exists():
        return []

    cache_dir = _default_cache_dir()

    records: list[SessionRecord] = []
    for path in base.rglob("*.jsonl"):
        run_id = path.stem
        if known_run_ids and run_id in known_run_ids:
            continue

        entries = load_jsonl_dict_lines(path)
        if not entries:
            continue

        # Skip subagent/sidechain transcripts — their content flows back to
        # the parent session via tool results, so extracting from both would
        # double-count. Also skip tiny sessions (< 6 conversation turns) which
        # are typically eval judge calls or trivial interactions.
        is_sidechain = any(e.get("isSidechain") for e in entries[:5])
        if is_sidechain:
            continue
        conv_turns = sum(
            1 for e in entries
            if e.get("type") in ("user", "assistant")
        )
        if conv_turns < 6:
            continue

        started_at: datetime | None = None
        repo_name: str | None = None
        cwd: str | None = None
        summaries: list[str] = []
        message_count = 0
        tool_calls = 0
        errors = 0
        total_tokens = 0

        for entry in entries:
            ts = (
                parse_timestamp(str(entry.get("timestamp") or ""))
                if entry.get("timestamp")
                else None
            )
            if ts:
                if started_at is None or ts < started_at:
                    started_at = ts
            if not repo_name:
                repo_name = entry.get("gitBranch") or None
            if not cwd:
                cwd = entry.get("cwd")

            entry_type = entry.get("type")
            if entry_type == "summary":
                summary = str(entry.get("summary") or "").strip()
                if summary:
                    summaries.append(summary)
            elif entry_type in {"user", "assistant", "system"}:
                message_count += 1

            message = entry.get("message")
            if isinstance(message, dict):
                usage = message.get("usage", {})
                if isinstance(usage, dict):
                    total_tokens += int(usage.get("input_tokens", 0) or 0)
                    total_tokens += int(usage.get("output_tokens", 0) or 0)
                content = message.get("content")
                if isinstance(content, list):
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        if block.get("type") == "tool_use":
                            tool_calls += 1
                        if block.get("type") == "tool_result" and block.get("is_error"):
                            errors += 1

        if not in_window(started_at, start, end):
            continue

        # Compact and export to cache
        raw_lines = path.read_text(encoding="utf-8").rstrip("\n").split("\n")
        cache_path = write_session_cache(cache_dir, run_id, raw_lines, compact_trace)

        records.append(
            SessionRecord(
                run_id=run_id,
                agent_type="claude",
                session_path=str(cache_path),
                start_time=started_at.isoformat() if started_at else None,
                repo_path=cwd,
                repo_name=repo_name,
                message_count=message_count,
                tool_call_count=tool_calls,
                error_count=errors,
                total_tokens=total_tokens,
                summaries=summaries[:5],
            )
        )

    records.sort(key=lambda r: r.start_time or "")
    return records
