"""OpenCode session adapter: reads sessions from OpenCode's SQLite database.

OpenCode stores all session data in a single SQLite database (opencode.db)
under ``~/.local/share/opencode/``.  The schema has three main tables:
``session``, ``message``, and ``part``.  Message and part payloads are JSON
blobs in the ``data`` column.  Timestamps are millisecond-epoch integers.

Like the Cursor adapter, each session is exported to an individual JSONL
cache file so the downstream sync pipeline and dashboard can read it as
plain text.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
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


def default_path() -> Path | None:
    """Return the default OpenCode storage root."""
    return Path("~/.local/share/opencode/").expanduser()


def _default_cache_dir() -> Path:
    """Return the default cache directory for exported OpenCode JSONL files."""
    return Path("~/.lerim/cache/opencode").expanduser()


def _resolve_db_path(root: Path) -> Path | None:
    """Find ``opencode.db`` under *root*."""
    if root.is_file() and root.name == "opencode.db":
        return root
    candidate = root / "opencode.db"
    if candidate.is_file():
        return candidate
    return None


def _json_col(raw: str | None) -> dict[str, Any]:
    """Parse a JSON text column, returning empty dict on failure."""
    if not raw:
        return {}
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    return obj if isinstance(obj, dict) else {}


def validate_connection(path: Path) -> dict[str, Any]:
    """Check that *path* resolves to a valid OpenCode DB with data."""
    db_path = _resolve_db_path(path)
    if not db_path:
        return {"ok": False, "error": f"No opencode.db found under {path}"}
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA query_only=ON")
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        for needed in ("session", "message", "part"):
            if needed not in tables:
                conn.close()
                return {
                    "ok": False,
                    "error": f"Table '{needed}' not found in {db_path}",
                }
        sessions = conn.execute("SELECT COUNT(*) FROM session").fetchone()[0]
        messages = conn.execute("SELECT COUNT(*) FROM message").fetchone()[0]
        conn.close()
        return {"ok": True, "sessions": sessions, "messages": messages}
    except sqlite3.Error as exc:
        return {"ok": False, "error": str(exc)}


def count_sessions(path: Path) -> int:
    """Count sessions in the OpenCode database."""
    if not path.exists():
        return 0
    db_path = _resolve_db_path(path)
    if not db_path:
        return 0
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA query_only=ON")
        n = conn.execute("SELECT COUNT(*) FROM session").fetchone()[0]
        conn.close()
        return n
    except sqlite3.Error:
        return 0


def find_session_path(session_id: str, traces_dir: Path | None = None) -> Path | None:
    """Return the JSONL cache path if it exists, else DB path if session exists."""
    session_id = session_id.strip()
    if not session_id:
        return None
    # Check cache first
    cache_path = _default_cache_dir() / f"{session_id}.jsonl"
    if cache_path.is_file():
        return cache_path
    # Fall back to DB
    root = traces_dir or default_path()
    if root is None or not root.exists():
        return None
    db_path = _resolve_db_path(root)
    if not db_path:
        return None
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA query_only=ON")
        row = conn.execute(
            "SELECT id FROM session WHERE id = ? LIMIT 1", (session_id,)
        ).fetchone()
        conn.close()
        return db_path if row else None
    except sqlite3.Error:
        return None


def _read_session_db(db_path: Path, session_id: str) -> ViewerSession | None:
    """Read one OpenCode session directly from the SQLite database."""
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA query_only=ON")

        sess_row = conn.execute(
            "SELECT directory, version, title FROM session WHERE id = ?",
            (session_id,),
        ).fetchone()
        if not sess_row:
            conn.close()
            return None
        cwd, version, title = sess_row

        msg_rows = conn.execute(
            "SELECT id, data FROM message WHERE session_id = ? ORDER BY time_created",
            (session_id,),
        ).fetchall()

        total_input = 0
        total_output = 0
        messages: list[ViewerMessage] = []

        for msg_id, msg_raw in msg_rows:
            msg = _json_col(msg_raw)
            role = str(msg.get("role") or "assistant")
            time_info = msg.get("time") or {}
            timestamp = parse_timestamp(time_info.get("created"))
            ts_iso = timestamp.isoformat() if timestamp else None

            tokens = msg.get("tokens") or {}
            total_input += int(tokens.get("input") or 0)
            total_output += int(tokens.get("output") or 0)
            total_output += int(tokens.get("reasoning") or 0)

            model_id = msg.get("modelID")

            part_rows = conn.execute(
                "SELECT data FROM part WHERE message_id = ? ORDER BY time_created",
                (msg_id,),
            ).fetchall()

            text_parts: list[str] = []
            for (part_raw,) in part_rows:
                part = _json_col(part_raw)
                ptype = part.get("type")
                if ptype == "text":
                    text = part.get("text")
                    if isinstance(text, str) and text.strip():
                        text_parts.append(text.strip())
                elif ptype == "tool":
                    tool_name = str(part.get("tool") or "tool")
                    state = part.get("state") or {}
                    tool_ts = parse_timestamp((state.get("time") or {}).get("start"))
                    messages.append(
                        ViewerMessage(
                            role="tool",
                            tool_name=tool_name,
                            tool_input=state.get("input"),
                            tool_output=state.get("output"),
                            timestamp=tool_ts.isoformat() if tool_ts else None,
                        )
                    )

            content = "\n".join(text_parts).strip()
            if content:
                messages.append(
                    ViewerMessage(
                        role=role,
                        content=content,
                        timestamp=ts_iso,
                        model=model_id,
                    )
                )

        conn.close()
        return ViewerSession(
            session_id=session_id,
            cwd=cwd,
            messages=messages,
            total_input_tokens=total_input,
            total_output_tokens=total_output,
            meta={"version": version, "title": title},
        )
    except sqlite3.Error:
        return None


def _read_session_jsonl(path: Path, session_id: str | None) -> ViewerSession | None:
    """Parse an exported OpenCode JSONL cache file into a ViewerSession."""
    lines = load_jsonl_dict_lines(path)
    if not lines:
        return None
    metadata = lines[0]
    resolved_id = session_id or metadata.get("session_id") or path.stem
    messages: list[ViewerMessage] = []
    total_input = 0
    total_output = 0
    for row in lines[1:]:
        role = row.get("role") or "assistant"
        if role == "tool":
            messages.append(
                ViewerMessage(
                    role="tool",
                    tool_name=row.get("tool_name"),
                    tool_input=row.get("tool_input"),
                    tool_output=row.get("tool_output"),
                    timestamp=row.get("timestamp"),
                )
            )
        else:
            content = row.get("content") or ""
            if content.strip():
                messages.append(
                    ViewerMessage(
                        role=role,
                        content=content,
                        timestamp=row.get("timestamp"),
                        model=row.get("model"),
                    )
                )
    total_input = int(metadata.get("total_input_tokens") or 0)
    total_output = int(metadata.get("total_output_tokens") or 0)
    return ViewerSession(
        session_id=resolved_id,
        cwd=metadata.get("cwd"),
        messages=messages,
        total_input_tokens=total_input,
        total_output_tokens=total_output,
        meta=metadata.get("meta") or {},
    )


def _export_session_jsonl(session: ViewerSession, out_dir: Path) -> Path:
    """Export a ViewerSession to a JSONL cache file, return the file path."""
    jsonl_path = out_dir / f"{session.session_id}.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as fh:
        # First line: session metadata
        fh.write(
            json.dumps(
                {
                    "session_id": session.session_id,
                    "cwd": session.cwd,
                    "total_input_tokens": session.total_input_tokens,
                    "total_output_tokens": session.total_output_tokens,
                    "meta": session.meta,
                },
                ensure_ascii=True,
            )
            + "\n"
        )
        # Remaining lines: one per message
        for msg in session.messages:
            row: dict[str, Any] = {"role": msg.role}
            if msg.content is not None:
                row["content"] = msg.content
            if msg.timestamp:
                row["timestamp"] = msg.timestamp
            if msg.model:
                row["model"] = msg.model
            if msg.tool_name:
                row["tool_name"] = msg.tool_name
            if msg.tool_input is not None:
                row["tool_input"] = msg.tool_input
            if msg.tool_output is not None:
                row["tool_output"] = msg.tool_output
            fh.write(json.dumps(row, ensure_ascii=True) + "\n")
    return jsonl_path


def read_session(
    session_path: Path, session_id: str | None = None
) -> ViewerSession | None:
    """Read one OpenCode session from JSONL cache or SQLite database.

    If *session_path* is a ``.jsonl`` file, reads the exported cache.
    If it points to ``opencode.db`` (or its parent), queries the DB directly.
    """
    if session_path.suffix == ".jsonl" and session_path.is_file():
        return _read_session_jsonl(session_path, session_id)
    # Try as DB path or directory containing opencode.db
    db_path = _resolve_db_path(session_path) if session_path.is_dir() else session_path
    if db_path and db_path.is_file() and session_id:
        return _read_session_db(db_path, session_id)
    return None


def iter_sessions(
    traces_dir: Path | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    known_run_hashes: dict[str, str] | None = None,
    cache_dir: Path | None = None,
) -> list[SessionRecord]:
    """Enumerate OpenCode sessions, export as JSONL, and build session records.

    Reads sessions from the SQLite database, exports each as a JSONL cache
    file in *cache_dir*, computes a content hash, and skips sessions whose
    hash is unchanged since the last sync.
    """
    root = traces_dir or default_path()
    if root is None or not root.exists():
        return []
    db_path = _resolve_db_path(root)
    if not db_path:
        return []

    out_dir = cache_dir or _default_cache_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    records: list[SessionRecord] = []
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA query_only=ON")
        rows = conn.execute(
            """SELECT id, directory, title, time_created FROM session \
ORDER BY time_created"""
        ).fetchall()
        conn.close()
    except sqlite3.Error:
        return []

    for sess_id, directory, title, time_created in rows:
        start_dt = parse_timestamp(time_created)
        if not in_window(start_dt, start, end):
            continue

        session = _read_session_db(db_path, session_id=sess_id)
        if session is None:
            continue

        # Export to JSONL cache file and compute hash
        jsonl_path = _export_session_jsonl(session, out_dir)
        file_hash = compute_file_hash(jsonl_path)
        if known_run_hashes and sess_id in known_run_hashes:
            if known_run_hashes[sess_id] == file_hash:
                continue

        summaries: list[str] = []
        for msg in session.messages:
            if msg.role in {"user", "assistant"} and (msg.content or "").strip():
                summaries.append((msg.content or "").strip()[:140])
            if len(summaries) >= 5:
                break

        message_count = len(
            [m for m in session.messages if m.role in {"user", "assistant"}]
        )
        tool_calls = len([m for m in session.messages if m.role == "tool"])
        records.append(
            SessionRecord(
                run_id=sess_id,
                agent_type="opencode",
                session_path=str(jsonl_path),
                start_time=start_dt.isoformat() if start_dt else None,
                repo_name=directory or None,
                message_count=message_count,
                tool_call_count=tool_calls,
                total_tokens=session.total_input_tokens + session.total_output_tokens,
                summaries=summaries,
                content_hash=file_hash,
            )
        )

    return records


# ---------------------------------------------------------------------------
# Self-test (runs against real OpenCode DB on this machine)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    root = default_path()
    if root is None or not root.exists():
        print(f"OpenCode not found at {root}, skipping self-test")
        sys.exit(0)

    db = _resolve_db_path(root)
    if not db:
        print(f"No opencode.db under {root}, skipping self-test")
        sys.exit(0)

    print(f"OpenCode storage: {root}")
    print(f"Database: {db} ({db.stat().st_size / 1024 / 1024:.1f} MB)")

    result = validate_connection(root)
    print(f"validate_connection: {result}")
    assert result["ok"], f"validate_connection failed: {result}"

    n = count_sessions(root)
    print(f"count_sessions: {n}")
    assert n > 0, "Expected at least one session"

    with tempfile.TemporaryDirectory() as tmp:
        cache = Path(tmp)
        sessions = iter_sessions(traces_dir=root, cache_dir=cache)
        print(f"iter_sessions: {len(sessions)} sessions")
        assert len(sessions) > 0, "Expected at least one session record"

        with_messages = [s for s in sessions if s.message_count > 0]
        print(f"  sessions with messages: {len(with_messages)}")
        assert with_messages, "Expected at least one session with messages"

        first = with_messages[0]
        jsonl_path = Path(first.session_path)
        assert jsonl_path.is_file(), f"JSONL not found: {jsonl_path}"
        lines = jsonl_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) > 1, f"Expected multiple lines, got {len(lines)}"
        print(f"  first JSONL ({jsonl_path.name}): {len(lines)} lines")

        # Verify JSONL round-trip via read_session
        viewer = read_session(jsonl_path, first.run_id)
        assert viewer is not None, "read_session(jsonl) returned None"
        assert viewer.messages, "read_session(jsonl) returned no messages"
        print(f"  read_session(jsonl): {len(viewer.messages)} messages")

        # Verify direct DB read still works
        viewer_db = _read_session_db(db, first.run_id)
        assert viewer_db is not None, "read_session(db) returned None"
        assert len(viewer_db.messages) == len(viewer.messages), (
            "JSONL round-trip message count mismatch"
        )
        print(f"  read_session(db):   {len(viewer_db.messages)} messages (matches)")

        print(
            f"    tokens: in={viewer.total_input_tokens} out={viewer.total_output_tokens}"
        )
        print(f"    title: {viewer.meta.get('title', '?')}")

        roles: dict[str, int] = {}
        for m in viewer.messages:
            roles[m.role] = roles.get(m.role, 0) + 1
        print(f"    roles: {roles}")

        # Verify find_session_path returns the cached JSONL
        found = find_session_path(first.run_id, root)
        print(f"  find_session_path: {found}")

    print("Self-test passed.")
