"""Cursor adapter: extracts sessions from Cursor's state.vscdb SQLite DB.

Cursor stores data in a single SQLite database (state.vscdb), table
cursorDiskKV.  Session metadata lives in ``composerData:<composerId>`` rows
and individual messages in ``bubbleId:<composerId>:<bubbleId>`` rows.

This adapter groups bubbles by composerId, exports each session as a JSONL
file (composerData first line, then one bubble per line), and returns
SessionRecords pointing to those files so the downstream sync pipeline can
read them as plain text.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from lerim.adapters.base import SessionRecord, ViewerMessage, ViewerSession
from lerim.adapters.common import (
    compute_file_hash,
    in_window,
    load_jsonl_dict_lines,
    parse_timestamp,
)


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def default_path() -> Path | None:
    """Return platform-specific default Cursor storage path."""
    if sys.platform == "darwin":
        return Path(
            "~/Library/Application Support/Cursor/User/globalStorage/"
        ).expanduser()
    if sys.platform.startswith("linux"):
        return Path("~/.config/Cursor/User/globalStorage/").expanduser()
    return Path("~/Library/Application Support/Cursor/User/globalStorage/").expanduser()


def _resolve_db_paths(root: Path) -> list[Path]:
    """Resolve candidate ``state.vscdb`` files from a root path."""
    if root.is_file():
        return [root]
    if (root / "state.vscdb").exists():
        return [root / "state.vscdb"]
    return [p for p in root.glob("*/state.vscdb") if p.is_file()]


def _default_cache_dir() -> Path:
    """Return the default cache directory for exported Cursor JSONL files."""
    return Path("~/.lerim/cache/cursor").expanduser()


# ---------------------------------------------------------------------------
# Value parsing helpers
# ---------------------------------------------------------------------------


def _parse_json_value(raw: str) -> Any | None:
    """Parse possibly double-encoded JSON values stored in Cursor DB rows."""
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _extract_text(value: Any) -> str:
    """Extract readable text from nested Cursor message payload values."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("text", "content", "message", "value"):
            if key in value:
                return _extract_text(value[key])
    if isinstance(value, list):
        parts = [p for p in (_extract_text(v) for v in value) if p]
        return "\n".join(parts)
    return str(value)


def _normalize_role(value: Any) -> str:
    """Normalize Cursor role values into user/assistant/tool.

    Handles integer bubble types (1=user, 2=assistant) and string aliases.
    """
    if isinstance(value, int):
        if value == 1:
            return "user"
        if value == 2:
            return "assistant"
        return "tool"
    role = str(value or "").lower()
    if role in {"user", "human", "human_user"}:
        return "user"
    if role in {"assistant", "ai", "bot", "model"}:
        return "assistant"
    if role in {"tool", "function"}:
        return "tool"
    return "assistant"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validate_connection(path: Path) -> dict[str, Any]:
    """Check that *path* resolves to a valid Cursor ``state.vscdb`` with data.

    Returns ``{"ok": True, "sessions": N, "messages": M}`` on success or
    ``{"ok": False, "error": "..."}`` on failure.
    """
    db_paths = _resolve_db_paths(path)
    if not db_paths:
        return {"ok": False, "error": f"No state.vscdb found under {path}"}

    total_sessions = 0
    total_messages = 0
    for db_path in db_paths:
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("PRAGMA query_only=ON")
            table = conn.execute(
                """SELECT name FROM sqlite_master \
WHERE type='table' AND name='cursorDiskKV'"""
            ).fetchone()
            if not table:
                return {
                    "ok": False,
                    "error": f"Table cursorDiskKV not found in {db_path}",
                }
            # Collect distinct composerIds from bubbleId rows
            composer_ids: set[str] = set()
            for (key,) in conn.execute(
                "SELECT key FROM cursorDiskKV WHERE key LIKE 'bubbleId:%'"
            ).fetchall():
                parts = key.split(":", 2)
                if len(parts) >= 3:
                    composer_ids.add(parts[1])
                    total_messages += 1
            total_sessions += len(composer_ids)
        except sqlite3.Error as exc:
            return {"ok": False, "error": str(exc)}
        finally:
            conn.close()

    return {"ok": True, "sessions": total_sessions, "messages": total_messages}


def count_sessions(path: Path) -> int:
    """Count Cursor composers that have at least one bubble message."""
    if not path.exists():
        return 0
    composer_ids: set[str] = set()
    for db_path in _resolve_db_paths(path):
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("PRAGMA query_only=ON")
            for (key,) in conn.execute(
                "SELECT key FROM cursorDiskKV WHERE key LIKE 'bubbleId:%'"
            ).fetchall():
                parts = key.split(":", 2)
                if len(parts) >= 3:
                    composer_ids.add(parts[1])
        except sqlite3.Error:
            continue
        finally:
            conn.close()
    return len(composer_ids)


def iter_sessions(
    traces_dir: Path | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    known_run_hashes: dict[str, str] | None = None,
    cache_dir: Path | None = None,
) -> list[SessionRecord]:
    """Enumerate Cursor sessions, export as JSONL, and build session records.

    Groups ``bubbleId`` rows by composerId, writes each session as a JSONL
    file in *cache_dir*, computes a content hash, and skips sessions whose
    hash is unchanged since the last sync.
    """
    root = traces_dir or default_path()
    if root is None or not root.exists():
        return []

    out_dir = cache_dir or _default_cache_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    records: list[SessionRecord] = []
    for db_path in _resolve_db_paths(root):
        composers: dict[str, dict] = {}
        bubbles: dict[str, list[dict]] = defaultdict(list)
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("PRAGMA query_only=ON")
            for key, raw in conn.execute(
                "SELECT key, value FROM cursorDiskKV WHERE key LIKE 'composerData:%'"
            ).fetchall():
                cid = key.split(":", 1)[1]
                parsed = _parse_json_value(raw)
                if isinstance(parsed, dict):
                    composers[cid] = parsed

            for key, raw in conn.execute(
                """SELECT key, value FROM cursorDiskKV \
WHERE key LIKE 'bubbleId:%' ORDER BY key"""
            ).fetchall():
                parts = key.split(":", 2)
                if len(parts) < 3:
                    continue
                cid = parts[1]
                parsed = _parse_json_value(raw)
                if isinstance(parsed, dict):
                    bubbles[cid].append(parsed)
        except sqlite3.Error:
            continue
        finally:
            conn.close()

        for cid, bubble_list in bubbles.items():
            metadata = composers.get(cid, {})
            started_at = parse_timestamp(metadata.get("createdAt"))
            if not in_window(started_at, start, end):
                continue

            # Export JSONL: metadata first line, then bubbles
            jsonl_path = out_dir / f"{cid}.jsonl"
            with jsonl_path.open("w", encoding="utf-8") as fh:
                fh.write(json.dumps(metadata) + "\n")
                for bubble in bubble_list:
                    fh.write(json.dumps(bubble) + "\n")

            # Hash-based change detection
            file_hash = compute_file_hash(jsonl_path)
            if known_run_hashes and cid in known_run_hashes:
                if known_run_hashes[cid] == file_hash:
                    continue

            message_count = sum(1 for b in bubble_list if b.get("type") in (1, 2))
            tool_count = sum(1 for b in bubble_list if b.get("type") not in (1, 2))
            summaries: list[str] = []
            for b in bubble_list:
                if b.get("type") == 1:
                    text = _extract_text(b.get("text")).strip()
                    if text:
                        summaries.append(text[:140])
                    if len(summaries) >= 5:
                        break

            records.append(
                SessionRecord(
                    run_id=cid,
                    agent_type="cursor",
                    session_path=str(jsonl_path),
                    start_time=started_at.isoformat() if started_at else None,
                    message_count=message_count,
                    tool_call_count=tool_count,
                    summaries=summaries,
                    content_hash=file_hash,
                )
            )

    return records


def find_session_path(session_id: str, traces_dir: Path | None = None) -> Path | None:
    """Find a Cursor session file by ID, checking cache first then DB."""
    session_id = session_id.strip()
    if not session_id:
        return None

    # Check cache first
    cache_path = _default_cache_dir() / f"{session_id}.jsonl"
    if cache_path.is_file():
        return cache_path

    # Fall back to scanning DB files
    root = traces_dir or default_path()
    if root is None or not root.exists():
        return None
    for db_path in _resolve_db_paths(root):
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("PRAGMA query_only=ON")
            row = conn.execute(
                "SELECT key FROM cursorDiskKV WHERE key LIKE ? LIMIT 1",
                (f"bubbleId:{session_id}:%",),
            ).fetchone()
            if row:
                return db_path
        except sqlite3.Error:
            continue
        finally:
            conn.close()
    return None


def read_session(
    session_path: Path, session_id: str | None = None
) -> ViewerSession | None:
    """Read one Cursor session from an exported JSONL or directly from SQLite.

    If *session_path* is a ``.jsonl`` file, reads the exported cache file.
    If it is a ``.vscdb`` file and *session_id* is provided, queries the DB
    directly (backward compat with dashboard).
    """
    if session_path.suffix == ".jsonl" and session_path.is_file():
        return _read_session_jsonl(session_path, session_id)
    if session_path.suffix == ".vscdb" and session_id:
        return _read_session_db(session_path, session_id)
    # Try resolving as directory containing state.vscdb
    db_path = session_path / "state.vscdb"
    if db_path.exists() and session_id:
        return _read_session_db(db_path, session_id)
    return None


def _read_session_jsonl(path: Path, session_id: str | None) -> ViewerSession | None:
    """Parse an exported Cursor JSONL file into a ViewerSession."""
    lines = load_jsonl_dict_lines(path)
    if not lines:
        return None
    metadata = lines[0]
    resolved_id = session_id or metadata.get("composerId") or path.stem
    messages: list[ViewerMessage] = []
    for bubble in lines[1:]:
        role = _normalize_role(bubble.get("type"))
        text = _extract_text(bubble.get("text"))
        if not text.strip():
            continue
        messages.append(ViewerMessage(role=role, content=text))
    return ViewerSession(session_id=resolved_id, messages=messages)


def _read_session_db(db_path: Path, session_id: str) -> ViewerSession | None:
    """Read one session directly from a Cursor SQLite DB by composerId."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA query_only=ON")
        rows = conn.execute(
            "SELECT value FROM cursorDiskKV WHERE key LIKE ? ORDER BY key",
            (f"bubbleId:{session_id}:%",),
        ).fetchall()
        messages: list[ViewerMessage] = []
        for (raw,) in rows:
            bubble = _parse_json_value(raw)
            if not isinstance(bubble, dict):
                continue
            role = _normalize_role(bubble.get("type"))
            text = _extract_text(bubble.get("text"))
            if not text.strip():
                continue
            messages.append(ViewerMessage(role=role, content=text))
        return (
            ViewerSession(session_id=session_id, messages=messages)
            if messages
            else None
        )
    except sqlite3.Error:
        return None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Self-test (runs against real Cursor DB on this machine)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    root = default_path()
    if root is None or not root.exists():
        print(f"Cursor not found at {root}, skipping self-test")
        sys.exit(0)

    print(f"Cursor storage: {root}")

    result = validate_connection(root)
    print(f"validate_connection: {result}")

    n = count_sessions(root)
    print(f"count_sessions: {n}")

    with tempfile.TemporaryDirectory() as tmp:
        cache = Path(tmp)
        sessions = iter_sessions(traces_dir=root, cache_dir=cache)
        print(f"iter_sessions: {len(sessions)} sessions")

        with_messages = [s for s in sessions if s.message_count > 0]
        assert with_messages, "Expected at least one session with messages"
        print(f"  sessions with messages: {len(with_messages)}")

        first = with_messages[0]
        jsonl_path = Path(first.session_path)
        assert jsonl_path.is_file(), f"JSONL not found: {jsonl_path}"
        lines = jsonl_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) > 1, f"Expected multiple lines, got {len(lines)}"
        print(f"  first JSONL ({jsonl_path.name}): {len(lines)} lines")

        viewer = read_session(jsonl_path, first.run_id)
        assert viewer is not None, "read_session returned None"
        assert viewer.messages, "read_session returned no messages"
        print(f"  read_session: {len(viewer.messages)} messages")

    print("Self-test passed.")
