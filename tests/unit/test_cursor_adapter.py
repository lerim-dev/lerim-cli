"""Unit tests for the Cursor adapter using temporary SQLite databases."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory


from lerim.adapters.cursor import (
    count_sessions,
    iter_sessions,
    read_session,
    validate_connection,
)


def _make_cursor_db(
    db_path: Path,
    composers: dict[str, dict],
    bubbles: list[tuple[str, str, dict]],
) -> None:
    """Create a test Cursor DB with given composers and bubbles.

    composers: {composerId: composerData_json_dict}
    bubbles: [(composerId, bubbleId, bubble_json_dict), ...]
    """
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE cursorDiskKV (key TEXT PRIMARY KEY, value TEXT)")
    for cid, data in composers.items():
        conn.execute(
            "INSERT INTO cursorDiskKV VALUES (?, ?)",
            (f"composerData:{cid}", json.dumps(data)),
        )
    for cid, bid, data in bubbles:
        conn.execute(
            "INSERT INTO cursorDiskKV VALUES (?, ?)",
            (f"bubbleId:{cid}:{bid}", json.dumps(data)),
        )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_iter_sessions_groups_bubbles_by_composer():
    """Two composers with 3 and 2 bubbles produce 2 SessionRecords."""
    with TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "state.vscdb"
        cache_dir = Path(tmp) / "cache"
        _make_cursor_db(
            db_path,
            composers={
                "aaa": {"composerId": "aaa", "createdAt": 1700000000000},
                "bbb": {"composerId": "bbb", "createdAt": 1700001000000},
            },
            bubbles=[
                ("aaa", "1", {"type": 1, "text": "hello from user"}),
                ("aaa", "2", {"type": 2, "text": "hello from assistant"}),
                ("aaa", "3", {"type": 1, "text": "follow-up"}),
                ("bbb", "1", {"type": 1, "text": "user msg"}),
                ("bbb", "2", {"type": 2, "text": "bot reply"}),
            ],
        )
        records = iter_sessions(traces_dir=Path(tmp), cache_dir=cache_dir)
        assert len(records) == 2

        by_id = {r.run_id: r for r in records}
        assert by_id["aaa"].message_count == 3
        assert by_id["bbb"].message_count == 2

        for rec in records:
            p = Path(rec.session_path)
            assert p.is_file()
            assert p.suffix == ".jsonl"


def test_iter_sessions_skips_composers_without_bubbles():
    """A composer with zero bubbles should not appear in results."""
    with TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "state.vscdb"
        cache_dir = Path(tmp) / "cache"
        _make_cursor_db(
            db_path,
            composers={"lonely": {"composerId": "lonely", "createdAt": 1700000000000}},
            bubbles=[],
        )
        records = iter_sessions(traces_dir=Path(tmp), cache_dir=cache_dir)
        assert records == []


def test_count_sessions_counts_composers_with_messages():
    """Only composers that have bubbleId rows are counted."""
    with TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "state.vscdb"
        _make_cursor_db(
            db_path,
            composers={
                "a": {"composerId": "a"},
                "b": {"composerId": "b"},
                "c": {"composerId": "c"},
            },
            bubbles=[
                ("a", "1", {"type": 1, "text": "hi"}),
                ("b", "1", {"type": 2, "text": "hey"}),
            ],
        )
        assert count_sessions(Path(tmp)) == 2


def test_exported_jsonl_contains_raw_data():
    """Exported JSONL preserves the raw DB data without normalization."""
    with TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "state.vscdb"
        cache_dir = Path(tmp) / "cache"
        composer_data = {
            "composerId": "raw",
            "createdAt": 1700000000000,
            "status": "active",
        }
        bubble_data = {
            "type": 1,
            "text": "raw message",
            "_v": 3,
            "lints": [{"code": "x"}],
            "extra_field": True,
        }
        _make_cursor_db(
            db_path,
            composers={"raw": composer_data},
            bubbles=[("raw", "b1", bubble_data)],
        )
        iter_sessions(traces_dir=Path(tmp), cache_dir=cache_dir)

        jsonl_path = cache_dir / "raw.jsonl"
        assert jsonl_path.is_file()
        lines = jsonl_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2

        meta = json.loads(lines[0])
        assert meta["composerId"] == "raw"
        assert meta["status"] == "active"

        bubble = json.loads(lines[1])
        assert bubble["type"] == 1
        assert bubble["text"] == "raw message"
        assert bubble["_v"] == 3
        assert bubble["lints"] == [{"code": "x"}]
        assert bubble["extra_field"] is True


def test_read_session_from_exported_jsonl():
    """read_session on an exported JSONL returns correct role mapping."""
    with TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "state.vscdb"
        cache_dir = Path(tmp) / "cache"
        _make_cursor_db(
            db_path,
            composers={"sess": {"composerId": "sess", "createdAt": 1700000000000}},
            bubbles=[
                ("sess", "1", {"type": 1, "text": "user question"}),
                ("sess", "2", {"type": 2, "text": "assistant answer"}),
                ("sess", "3", {"type": 1, "text": "follow up"}),
            ],
        )
        records = iter_sessions(traces_dir=Path(tmp), cache_dir=cache_dir)
        assert len(records) == 1

        session = read_session(Path(records[0].session_path), "sess")
        assert session is not None
        assert session.session_id == "sess"
        assert len(session.messages) == 3
        assert session.messages[0].role == "user"
        assert session.messages[0].content == "user question"
        assert session.messages[1].role == "assistant"
        assert session.messages[1].content == "assistant answer"
        assert session.messages[2].role == "user"


def test_validate_connection_passes_on_valid_db():
    """A valid DB with composerData and bubbleId rows passes validation."""
    with TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "state.vscdb"
        _make_cursor_db(
            db_path,
            composers={"v": {"composerId": "v"}},
            bubbles=[
                ("v", "1", {"type": 1, "text": "hi"}),
                ("v", "2", {"type": 2, "text": "hello"}),
            ],
        )
        result = validate_connection(Path(tmp))
        assert result["ok"] is True
        assert result["sessions"] == 1
        assert result["messages"] == 2


def test_validate_connection_fails_on_missing_table():
    """A SQLite DB without cursorDiskKV table should fail validation."""
    with TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "state.vscdb"
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE other_table (id INTEGER)")
        conn.commit()
        conn.close()

        result = validate_connection(Path(tmp))
        assert result["ok"] is False
        assert "cursorDiskKV" in result["error"]


def test_validate_connection_warns_on_empty_conversations():
    """DB with composerData but no bubbles reports 0 messages/sessions."""
    with TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "state.vscdb"
        _make_cursor_db(
            db_path,
            composers={"empty": {"composerId": "empty"}},
            bubbles=[],
        )
        result = validate_connection(Path(tmp))
        assert result["ok"] is True
        assert result["messages"] == 0
        assert result["sessions"] == 0


def test_iter_sessions_returns_content_hash():
    """iter_sessions populates content_hash on returned records."""
    with TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "state.vscdb"
        cache_dir = Path(tmp) / "cache"
        _make_cursor_db(
            db_path,
            composers={"hh": {"composerId": "hh", "createdAt": 1700000000000}},
            bubbles=[("hh", "1", {"type": 1, "text": "hello"})],
        )
        records = iter_sessions(traces_dir=Path(tmp), cache_dir=cache_dir)
        assert len(records) == 1
        assert records[0].content_hash is not None
        assert len(records[0].content_hash) == 64


def test_iter_sessions_skips_unchanged_hash():
    """iter_sessions skips sessions whose content hash has not changed."""
    with TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "state.vscdb"
        cache_dir = Path(tmp) / "cache"
        _make_cursor_db(
            db_path,
            composers={"cc": {"composerId": "cc", "createdAt": 1700000000000}},
            bubbles=[("cc", "1", {"type": 1, "text": "hi"})],
        )
        first = iter_sessions(traces_dir=Path(tmp), cache_dir=cache_dir)
        assert len(first) == 1
        old_hash = first[0].content_hash

        # Same DB content → same hash → should be skipped
        records = iter_sessions(
            traces_dir=Path(tmp),
            cache_dir=cache_dir,
            known_run_hashes={"cc": old_hash},
        )
        assert len(records) == 0


def test_iter_sessions_returns_changed_cursor_session():
    """iter_sessions returns a session when its DB content changed."""
    with TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "state.vscdb"
        cache_dir = Path(tmp) / "cache"
        _make_cursor_db(
            db_path,
            composers={"chg": {"composerId": "chg", "createdAt": 1700000000000}},
            bubbles=[("chg", "1", {"type": 1, "text": "original"})],
        )
        first = iter_sessions(traces_dir=Path(tmp), cache_dir=cache_dir)
        old_hash = first[0].content_hash

        # Add a new bubble (simulates resumed chat)
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO cursorDiskKV VALUES (?, ?)",
            (
                "bubbleId:chg:2",
                json.dumps({"type": 2, "text": "new assistant reply"}),
            ),
        )
        conn.commit()
        conn.close()

        records = iter_sessions(
            traces_dir=Path(tmp),
            cache_dir=cache_dir,
            known_run_hashes={"chg": old_hash},
        )
        assert len(records) == 1
        assert records[0].content_hash != old_hash
