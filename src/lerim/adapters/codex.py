"""Codex session adapter for normalized viewer and index records."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from lerim.adapters.base import SessionRecord, ViewerMessage, ViewerSession
from lerim.adapters.common import (
    compact_jsonl,
    count_non_empty_files,
    in_window,
    load_jsonl_dict_lines,
    make_canonical_entry,
    normalize_timestamp_iso,
    parse_timestamp,
    write_session_cache,
)


def _clean_entry(obj: dict[str, Any]) -> dict[str, Any] | None:
    """Transform a Codex JSONL entry into the canonical compacted schema.

    Drops: session_meta (metadata), event_msg (duplicates of response_items),
           developer messages (system prompts), reasoning blocks.
    Transforms response_item entries into canonical
    ``{"type", "message": {"role", "content"}, "timestamp"}`` records.
    """
    line_type = obj.get("type")

    # 1. Drop session metadata entirely
    if line_type == "session_meta":
        return None

    # 2. Drop event_msg entirely (duplicates of response_items)
    if line_type == "event_msg":
        return None

    # 3. Only response_item entries carry conversation data
    if line_type != "response_item":
        return None

    payload = obj.get("payload")
    if not isinstance(payload, dict):
        return None

    timestamp = normalize_timestamp_iso(
        obj.get("timestamp") or payload.get("timestamp")
    )
    ptype = payload.get("type")

    # 3a. Drop developer (system prompt) messages
    if ptype == "message" and payload.get("role") == "developer":
        return None

    # 3b. User messages
    if ptype == "message" and payload.get("role") == "user":
        text = _extract_message_text(payload.get("content"))
        if not text:
            return None
        return make_canonical_entry("user", "user", text, timestamp)

    # 3c. Assistant messages -- strip <think> blocks
    if ptype == "message" and payload.get("role") == "assistant":
        text = _extract_message_text(payload.get("content"))
        if not text:
            return None
        text = re.sub(r"<think>[\s\S]*?</think>", "[thinking cleared]", text)
        return make_canonical_entry("assistant", "assistant", text, timestamp)

    # 3d. Function calls
    if ptype == "function_call":
        content = [
            {
                "type": "tool_use",
                "name": payload.get("name", ""),
                "input": payload.get("arguments", ""),
            }
        ]
        return make_canonical_entry("assistant", "assistant", content, timestamp)

    # 3e. Function call outputs -- clear content (idempotent)
    if ptype == "function_call_output":
        output = payload.get("output", "")
        output_str = str(output)
        if output_str.startswith("[cleared:"):
            descriptor = output_str
        else:
            descriptor = f"[cleared: {len(output_str)} chars]"
        content = [{"type": "tool_result", "content": descriptor}]
        return make_canonical_entry("assistant", "assistant", content, timestamp)

    # 3f. Reasoning blocks -- drop
    if ptype == "reasoning":
        return None

    # 4. Any other payload type -- drop
    return None


def compact_trace(raw_text: str) -> str:
    """Strip tool outputs and noise from Codex session JSONL."""
    return compact_jsonl(raw_text, _clean_entry)


def _default_cache_dir() -> Path:
    """Return the default cache directory for compacted Codex JSONL files."""
    return Path("~/.lerim/cache/codex").expanduser()


def default_path() -> Path | None:
    """Return the default Codex session trace directory."""
    return Path("~/.codex/sessions/").expanduser()


def count_sessions(path: Path) -> int:
    """Count readable non-empty Codex session JSONL files."""
    return count_non_empty_files(path, "*.jsonl")


def _extract_message_text(content: object) -> str | None:
    """Normalize message payload content to plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        if parts:
            return "\n".join(parts)
    return None


def find_session_path(session_id: str, traces_dir: Path | None = None) -> Path | None:
    """Find a Codex JSONL session by exact stem or partial filename match."""
    base = traces_dir or default_path()
    if base is None or not base.exists():
        return None
    session_id = session_id.strip()
    if not session_id:
        return None
    for path in base.rglob("*.jsonl"):
        if path.stem == session_id or session_id in path.name:
            return path
    return None


def read_session(
    session_path: Path, session_id: str | None = None
) -> ViewerSession | None:
    """Parse a Codex trace into normalized user/assistant/tool messages."""
    messages: list[ViewerMessage] = []
    tool_messages: dict[str, ViewerMessage] = {}
    event_messages: list[ViewerMessage] = []
    has_response_items = False
    total_input = 0
    total_output = 0

    for entry in load_jsonl_dict_lines(session_path):
        entry_type = entry.get("type")
        payload = entry.get("payload") or {}
        timestamp = entry.get("timestamp") or payload.get("timestamp")

        if entry_type == "event_msg":
            event_type = payload.get("type")
            if event_type == "token_count":
                info = payload.get("info", {})
                usage = (
                    info.get("last_token_usage", {}) if isinstance(info, dict) else {}
                )
                total_input += int(usage.get("input_tokens", 0) or 0)
                total_output += int(usage.get("output_tokens", 0) or 0)
                total_output += int(usage.get("reasoning_output_tokens", 0) or 0)
            elif event_type in ("user_message", "agent_message"):
                role = "user" if event_type == "user_message" else "assistant"
                text = payload.get("message")
                if isinstance(text, str) and text.strip():
                    event_messages.append(
                        ViewerMessage(
                            role=role, content=text.strip(), timestamp=timestamp
                        )
                    )
            continue

        if entry_type != "response_item":
            continue

        payload_type = payload.get("type")
        if payload_type == "message":
            has_response_items = True
            role = payload.get("role")
            text = _extract_message_text(payload.get("content"))
            if role and text:
                messages.append(
                    ViewerMessage(role=str(role), content=text, timestamp=timestamp)
                )
        elif payload_type in ("function_call", "custom_tool_call"):
            has_response_items = True
            tool_id = str(payload.get("call_id") or payload.get("id") or "")
            tool_name = str(payload.get("name") or "tool")
            tool_input = (
                payload.get("arguments")
                if payload_type == "function_call"
                else payload.get("input")
            )
            tool_msg = ViewerMessage(
                role="tool",
                tool_name=tool_name,
                tool_input=tool_input,
                timestamp=timestamp,
            )
            tool_messages[tool_id] = tool_msg
            messages.append(tool_msg)
        elif payload_type in ("function_call_output", "custom_tool_call_output"):
            has_response_items = True
            call_id = str(payload.get("call_id") or payload.get("id") or "")
            if call_id in tool_messages:
                tool_messages[call_id].tool_output = payload.get("output")
            else:
                messages.append(
                    ViewerMessage(
                        role="tool",
                        tool_name="tool",
                        tool_output=payload.get("output"),
                        timestamp=timestamp,
                    )
                )

    if not has_response_items and event_messages:
        messages = event_messages

    return ViewerSession(
        session_id=session_id or session_path.stem,
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
    """Enumerate Codex sessions, skipping those already indexed by ID."""
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
        start_time: datetime | None = None
        repo_name: str | None = None
        cwd: str | None = None
        message_count = 0
        tool_calls = 0
        errors = 0
        total_tokens = 0
        summaries: list[str] = []

        for entry in entries:
            payload = entry.get("payload") or {}
            ts = parse_timestamp(
                str(entry.get("timestamp") or payload.get("timestamp") or "")
            )
            if ts:
                if start_time is None or ts < start_time:
                    start_time = ts

            if entry.get("type") == "session_meta" and isinstance(payload, dict):
                git = payload.get("git") or {}
                if isinstance(git, dict) and not repo_name:
                    repo_name = git.get("branch") or None
                if not cwd:
                    cwd = payload.get("cwd") or None

            if entry.get("type") == "event_msg":
                ev_type = payload.get("type")
                if ev_type in {"user_message", "agent_message"}:
                    message_count += 1
                    msg_text = str(payload.get("message") or "").strip()
                    if msg_text:
                        summaries.append(msg_text[:140])
                if ev_type == "token_count":
                    usage = (payload.get("info") or {}).get("last_token_usage", {})
                    if isinstance(usage, dict):
                        total_tokens += int(usage.get("input_tokens", 0) or 0)
                        total_tokens += int(usage.get("output_tokens", 0) or 0)
                        total_tokens += int(
                            usage.get("reasoning_output_tokens", 0) or 0
                        )

            if entry.get("type") == "response_item" and isinstance(payload, dict):
                ptype = payload.get("type")
                if ptype in {"function_call", "custom_tool_call"}:
                    tool_calls += 1
                if ptype in {"function_call_output", "custom_tool_call_output"}:
                    output = str(payload.get("output") or "")
                    if "error" in output.lower():
                        errors += 1

        if not in_window(start_time, start, end):
            continue

        # Compact and export to cache
        raw_lines = path.read_text(encoding="utf-8").rstrip("\n").split("\n")
        cache_path = write_session_cache(cache_dir, run_id, raw_lines, compact_trace)

        records.append(
            SessionRecord(
                run_id=run_id,
                agent_type="codex",
                session_path=str(cache_path),
                start_time=start_time.isoformat() if start_time else None,
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
