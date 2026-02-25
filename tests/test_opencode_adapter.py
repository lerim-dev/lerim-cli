"""Unit tests for the OpenCode session adapter using in-memory SQLite databases."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from lerim.adapters.opencode import (
    count_sessions,
    read_session,
    validate_connection,
)
from lerim.adapters.base import ViewerSession


def _make_opencode_db(db_path: Path) -> None:
    """Create a minimal OpenCode SQLite DB with test data."""
    conn = sqlite3.connect(db_path)
    conn.execute("""CREATE TABLE session (
        id TEXT PRIMARY KEY,
        directory TEXT,
        version TEXT,
        title TEXT,
        time_created INTEGER
    )""")
    conn.execute("""CREATE TABLE message (
        id TEXT PRIMARY KEY,
        session_id TEXT,
        data TEXT,
        time_created INTEGER
    )""")
    conn.execute("""CREATE TABLE part (
        id TEXT PRIMARY KEY,
        message_id TEXT,
        data TEXT,
        time_created INTEGER
    )""")
    # Insert a session
    conn.execute(
        "INSERT INTO session VALUES (?, ?, ?, ?, ?)",
        ("sess-1", "/tmp/project", "1.0", "Test Session", 1708000000000),
    )
    # Insert messages
    conn.execute(
        "INSERT INTO message VALUES (?, ?, ?, ?)",
        (
            "msg-1",
            "sess-1",
            json.dumps({"role": "user", "tokens": {"input": 10, "output": 0}}),
            1708000001000,
        ),
    )
    conn.execute(
        "INSERT INTO message VALUES (?, ?, ?, ?)",
        (
            "msg-2",
            "sess-1",
            json.dumps(
                {
                    "role": "assistant",
                    "tokens": {"input": 0, "output": 50},
                    "modelID": "gpt-4",
                }
            ),
            1708000002000,
        ),
    )
    # Insert parts
    conn.execute(
        "INSERT INTO part VALUES (?, ?, ?, ?)",
        (
            "part-1",
            "msg-1",
            json.dumps({"type": "text", "text": "User question here"}),
            1708000001000,
        ),
    )
    conn.execute(
        "INSERT INTO part VALUES (?, ?, ?, ?)",
        (
            "part-2",
            "msg-2",
            json.dumps({"type": "text", "text": "Assistant answer here"}),
            1708000002000,
        ),
    )
    conn.commit()
    conn.close()


def test_read_session_from_sqlite(tmp_path):
    """Read session from an in-memory SQLite DB mimicking OpenCode schema."""
    db_path = tmp_path / "opencode.db"
    _make_opencode_db(db_path)
    session = read_session(tmp_path, session_id="sess-1")
    assert session is not None
    assert isinstance(session, ViewerSession)
    assert session.session_id == "sess-1"
    user_msgs = [m for m in session.messages if m.role == "user"]
    asst_msgs = [m for m in session.messages if m.role == "assistant"]
    assert len(user_msgs) >= 1
    assert len(asst_msgs) >= 1
    assert "User question" in user_msgs[0].content
    assert "Assistant answer" in asst_msgs[0].content


def test_jsonl_export_roundtrip(tmp_path):
    """Export ViewerSession to JSONL, re-read, verify identical content."""
    from lerim.adapters.opencode import _export_session_jsonl, _read_session_jsonl

    session = ViewerSession(
        session_id="roundtrip-test",
        cwd="/tmp",
        messages=[
            __import__("lerim.adapters.base", fromlist=["ViewerMessage"]).ViewerMessage(
                role="user", content="Hello"
            ),
            __import__("lerim.adapters.base", fromlist=["ViewerMessage"]).ViewerMessage(
                role="assistant", content="World"
            ),
        ],
        total_input_tokens=100,
        total_output_tokens=200,
    )
    jsonl_path = _export_session_jsonl(session, tmp_path)
    assert jsonl_path.is_file()
    reloaded = _read_session_jsonl(jsonl_path, "roundtrip-test")
    assert reloaded is not None
    assert len(reloaded.messages) == 2
    assert reloaded.total_input_tokens == 100
    assert reloaded.total_output_tokens == 200


def test_count_sessions(tmp_path):
    """count_sessions on mock DB."""
    db_path = tmp_path / "opencode.db"
    _make_opencode_db(db_path)
    assert count_sessions(tmp_path) == 1


def test_validate_connection_valid(tmp_path):
    """validate_connection passes on well-formed DB."""
    db_path = tmp_path / "opencode.db"
    _make_opencode_db(db_path)
    result = validate_connection(tmp_path)
    assert result["ok"] is True
    assert result["sessions"] == 1
    assert result["messages"] == 2


def test_validate_connection_missing(tmp_path):
    """validate_connection fails on missing DB."""
    result = validate_connection(tmp_path)
    assert result["ok"] is False
    assert "error" in result
