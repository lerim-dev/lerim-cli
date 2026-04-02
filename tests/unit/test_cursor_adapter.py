"""Unit tests for the Cursor adapter using temporary SQLite databases."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory

from lerim.adapters.cursor import (
	_extract_text,
	_normalize_role,
	_parse_json_value,
	_read_session_db,
	_resolve_db_paths,
	compact_trace,
	count_sessions,
	default_path,
	find_session_path,
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
# _parse_json_value tests
# ---------------------------------------------------------------------------


def test_parse_json_value_plain_dict():
	"""Normal JSON dict is parsed directly."""
	raw = json.dumps({"key": "value"})
	assert _parse_json_value(raw) == {"key": "value"}


def test_parse_json_value_double_encoded():
	"""Double-encoded JSON string is unwrapped to the inner object."""
	inner = {"nested": True}
	raw = json.dumps(json.dumps(inner))
	assert _parse_json_value(raw) == inner


def test_parse_json_value_plain_string():
	"""A JSON string that is not itself JSON returns the plain string."""
	raw = json.dumps("hello world")
	assert _parse_json_value(raw) == "hello world"


def test_parse_json_value_invalid_json():
	"""Invalid JSON returns None."""
	assert _parse_json_value("not json at all {{{") is None


def test_parse_json_value_integer():
	"""A JSON integer is returned as-is (not a string, no inner parse)."""
	raw = json.dumps(42)
	assert _parse_json_value(raw) == 42


def test_parse_json_value_list():
	"""A JSON list is returned as-is."""
	raw = json.dumps([1, 2, 3])
	assert _parse_json_value(raw) == [1, 2, 3]


# ---------------------------------------------------------------------------
# _extract_text tests
# ---------------------------------------------------------------------------


def test_extract_text_none():
	"""None input returns empty string."""
	assert _extract_text(None) == ""


def test_extract_text_plain_string():
	"""Plain string is returned unchanged."""
	assert _extract_text("hello") == "hello"


def test_extract_text_dict_with_text_key():
	"""Dict with 'text' key extracts recursively."""
	assert _extract_text({"text": "inner"}) == "inner"


def test_extract_text_dict_with_content_key():
	"""Dict with 'content' key extracts recursively."""
	assert _extract_text({"content": "body"}) == "body"


def test_extract_text_dict_with_message_key():
	"""Dict with 'message' key extracts recursively."""
	assert _extract_text({"message": "msg"}) == "msg"


def test_extract_text_dict_with_value_key():
	"""Dict with 'value' key extracts recursively."""
	assert _extract_text({"value": "val"}) == "val"


def test_extract_text_dict_nested():
	"""Nested dict resolves through multiple levels."""
	assert _extract_text({"text": {"content": "deep"}}) == "deep"


def test_extract_text_dict_no_known_key():
	"""Dict without known keys returns str() of the dict."""
	val = {"unknown": "x"}
	assert _extract_text(val) == str(val)


def test_extract_text_list():
	"""List of values joins non-empty extracted parts."""
	result = _extract_text(["hello", {"text": "world"}, None])
	assert result == "hello\nworld"


def test_extract_text_integer():
	"""Non-string, non-dict, non-list falls back to str()."""
	assert _extract_text(42) == "42"


# ---------------------------------------------------------------------------
# _normalize_role tests
# ---------------------------------------------------------------------------


def test_normalize_role_int_user():
	"""Integer 1 maps to 'user'."""
	assert _normalize_role(1) == "user"


def test_normalize_role_int_assistant():
	"""Integer 2 maps to 'assistant'."""
	assert _normalize_role(2) == "assistant"


def test_normalize_role_int_other():
	"""Integer other than 1 or 2 maps to 'tool'."""
	assert _normalize_role(3) == "tool"
	assert _normalize_role(0) == "tool"
	assert _normalize_role(99) == "tool"


def test_normalize_role_string_user_aliases():
	"""String user aliases map to 'user'."""
	for alias in ("user", "human", "human_user", "User", "HUMAN"):
		assert _normalize_role(alias) == "user"


def test_normalize_role_string_assistant_aliases():
	"""String assistant aliases map to 'assistant'."""
	for alias in ("assistant", "ai", "bot", "model", "Assistant", "AI"):
		assert _normalize_role(alias) == "assistant"


def test_normalize_role_string_tool_aliases():
	"""String tool aliases map to 'tool'."""
	for alias in ("tool", "function", "Tool"):
		assert _normalize_role(alias) == "tool"


def test_normalize_role_unknown_string():
	"""Unknown string defaults to 'assistant'."""
	assert _normalize_role("something_else") == "assistant"


def test_normalize_role_none():
	"""None defaults to 'assistant'."""
	assert _normalize_role(None) == "assistant"


# ---------------------------------------------------------------------------
# _resolve_db_paths tests
# ---------------------------------------------------------------------------


def test_resolve_db_paths_file(tmp_path):
	"""Direct file path returns a single-element list."""
	db = tmp_path / "state.vscdb"
	db.touch()
	assert _resolve_db_paths(db) == [db]


def test_resolve_db_paths_dir_with_state(tmp_path):
	"""Directory containing state.vscdb returns it."""
	db = tmp_path / "state.vscdb"
	db.touch()
	assert _resolve_db_paths(tmp_path) == [db]


def test_resolve_db_paths_nested(tmp_path):
	"""Glob finds state.vscdb in subdirectories."""
	sub = tmp_path / "subdir"
	sub.mkdir()
	db = sub / "state.vscdb"
	db.touch()
	result = _resolve_db_paths(tmp_path)
	assert db in result


def test_resolve_db_paths_empty(tmp_path):
	"""Empty directory returns empty list."""
	assert _resolve_db_paths(tmp_path) == []


# ---------------------------------------------------------------------------
# default_path test
# ---------------------------------------------------------------------------


def test_default_path_returns_path():
	"""default_path returns a Path object on all platforms."""
	result = default_path()
	assert result is not None
	assert isinstance(result, Path)


# ---------------------------------------------------------------------------
# validate_connection tests
# ---------------------------------------------------------------------------


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


def test_validate_connection_no_db_found(tmp_path):
	"""validate_connection reports error when no state.vscdb exists."""
	result = validate_connection(tmp_path)
	assert result["ok"] is False
	assert "No state.vscdb" in result["error"]


def test_validate_connection_multiple_composers(tmp_path):
	"""validate_connection counts distinct composerIds across many bubbles."""
	db_path = tmp_path / "state.vscdb"
	_make_cursor_db(
		db_path,
		composers={},
		bubbles=[
			("c1", "1", {"type": 1, "text": "a"}),
			("c1", "2", {"type": 2, "text": "b"}),
			("c2", "1", {"type": 1, "text": "c"}),
		],
	)
	result = validate_connection(tmp_path)
	assert result["ok"] is True
	assert result["sessions"] == 2
	assert result["messages"] == 3


def test_validate_connection_malformed_bubbleid_key(tmp_path):
	"""Bubble keys without enough ':' parts are ignored in the count."""
	db_path = tmp_path / "state.vscdb"
	conn = sqlite3.connect(db_path)
	conn.execute("CREATE TABLE cursorDiskKV (key TEXT PRIMARY KEY, value TEXT)")
	conn.execute(
		"INSERT INTO cursorDiskKV VALUES (?, ?)",
		("bubbleId:onlyonepart", json.dumps({"type": 1})),
	)
	conn.commit()
	conn.close()
	result = validate_connection(tmp_path)
	assert result["ok"] is True
	assert result["sessions"] == 0
	assert result["messages"] == 0


# ---------------------------------------------------------------------------
# count_sessions tests
# ---------------------------------------------------------------------------


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


def test_count_sessions_nonexistent_path(tmp_path):
	"""Nonexistent path returns 0."""
	assert count_sessions(tmp_path / "nope") == 0


def test_count_sessions_empty_db(tmp_path):
	"""DB with no bubbles returns 0."""
	db_path = tmp_path / "state.vscdb"
	_make_cursor_db(db_path, composers={"a": {}}, bubbles=[])
	assert count_sessions(tmp_path) == 0


# ---------------------------------------------------------------------------
# iter_sessions tests
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


def test_iter_sessions_skips_known_ids():
	"""iter_sessions skips sessions whose run_id is already known."""
	with TemporaryDirectory() as tmp:
		db_path = Path(tmp) / "state.vscdb"
		cache_dir = Path(tmp) / "cache"
		_make_cursor_db(
			db_path,
			composers={
				"known": {"composerId": "known", "createdAt": 1700000000000},
				"new": {"composerId": "new", "createdAt": 1700001000000},
			},
			bubbles=[
				("known", "1", {"type": 1, "text": "hi"}),
				("new", "1", {"type": 1, "text": "hello"}),
			],
		)
		# Skip "known" by providing its ID
		records = iter_sessions(
			traces_dir=Path(tmp),
			cache_dir=cache_dir,
			known_run_ids={"known"},
		)
		assert len(records) == 1
		assert records[0].run_id == "new"


def test_iter_sessions_nonexistent_dir(tmp_path):
	"""Nonexistent traces_dir returns empty list."""
	records = iter_sessions(traces_dir=tmp_path / "nope")
	assert records == []


def test_iter_sessions_summaries_collected(tmp_path):
	"""User bubble text is collected as summaries (up to 5, truncated to 140 chars)."""
	db_path = tmp_path / "state.vscdb"
	cache_dir = tmp_path / "cache"
	_make_cursor_db(
		db_path,
		composers={"s": {"composerId": "s", "createdAt": 1700000000000}},
		bubbles=[
			("s", str(i), {"type": 1, "text": f"user message {i}"})
			for i in range(7)
		],
	)
	records = iter_sessions(traces_dir=tmp_path, cache_dir=cache_dir)
	assert len(records) == 1
	# Should collect at most 5 summaries
	assert len(records[0].summaries) == 5
	assert records[0].summaries[0] == "user message 0"


def test_iter_sessions_tool_count(tmp_path):
	"""Bubbles with type not in (1, 2) are counted as tool calls."""
	db_path = tmp_path / "state.vscdb"
	cache_dir = tmp_path / "cache"
	_make_cursor_db(
		db_path,
		composers={"t": {"composerId": "t", "createdAt": 1700000000000}},
		bubbles=[
			("t", "1", {"type": 1, "text": "user"}),
			("t", "2", {"type": 2, "text": "assistant"}),
			("t", "3", {"type": 3, "text": "tool result"}),
			("t", "4", {"type": 4, "text": "another tool"}),
		],
	)
	records = iter_sessions(traces_dir=tmp_path, cache_dir=cache_dir)
	assert len(records) == 1
	assert records[0].message_count == 2
	assert records[0].tool_call_count == 2


def test_iter_sessions_agent_type_is_cursor(tmp_path):
	"""SessionRecord agent_type is 'cursor'."""
	db_path = tmp_path / "state.vscdb"
	cache_dir = tmp_path / "cache"
	_make_cursor_db(
		db_path,
		composers={"x": {"composerId": "x", "createdAt": 1700000000000}},
		bubbles=[("x", "1", {"type": 1, "text": "hi"})],
	)
	records = iter_sessions(traces_dir=tmp_path, cache_dir=cache_dir)
	assert records[0].agent_type == "cursor"


def test_iter_sessions_sorted_by_start_time(tmp_path):
	"""Records are sorted by start_time."""
	db_path = tmp_path / "state.vscdb"
	cache_dir = tmp_path / "cache"
	_make_cursor_db(
		db_path,
		composers={
			"late": {"composerId": "late", "createdAt": 1700002000000},
			"early": {"composerId": "early", "createdAt": 1700000000000},
		},
		bubbles=[
			("late", "1", {"type": 1, "text": "late"}),
			("early", "1", {"type": 1, "text": "early"}),
		],
	)
	records = iter_sessions(traces_dir=tmp_path, cache_dir=cache_dir)
	assert records[0].run_id == "early"
	assert records[1].run_id == "late"


def test_iter_sessions_malformed_bubble_key_skipped(tmp_path):
	"""Bubble keys with fewer than 3 ':' parts are silently skipped."""
	db_path = tmp_path / "state.vscdb"
	cache_dir = tmp_path / "cache"
	conn = sqlite3.connect(db_path)
	conn.execute("CREATE TABLE cursorDiskKV (key TEXT PRIMARY KEY, value TEXT)")
	conn.execute(
		"INSERT INTO cursorDiskKV VALUES (?, ?)",
		("bubbleId:badkey", json.dumps({"type": 1, "text": "orphan"})),
	)
	conn.execute(
		"INSERT INTO cursorDiskKV VALUES (?, ?)",
		("bubbleId:good:1", json.dumps({"type": 1, "text": "ok"})),
	)
	conn.commit()
	conn.close()
	records = iter_sessions(traces_dir=tmp_path, cache_dir=cache_dir)
	assert len(records) == 1
	assert records[0].run_id == "good"


def test_iter_sessions_double_encoded_values(tmp_path):
	"""Double-encoded JSON values in DB are parsed correctly."""
	db_path = tmp_path / "state.vscdb"
	cache_dir = tmp_path / "cache"
	composer_data = {"composerId": "denc", "createdAt": 1700000000000}
	bubble_data = {"type": 1, "text": "double encoded"}
	conn = sqlite3.connect(db_path)
	conn.execute("CREATE TABLE cursorDiskKV (key TEXT PRIMARY KEY, value TEXT)")
	# Double-encode: json.dumps wraps the already-dumped string
	conn.execute(
		"INSERT INTO cursorDiskKV VALUES (?, ?)",
		("composerData:denc", json.dumps(json.dumps(composer_data))),
	)
	conn.execute(
		"INSERT INTO cursorDiskKV VALUES (?, ?)",
		("bubbleId:denc:1", json.dumps(json.dumps(bubble_data))),
	)
	conn.commit()
	conn.close()
	records = iter_sessions(traces_dir=tmp_path, cache_dir=cache_dir)
	assert len(records) == 1
	assert records[0].run_id == "denc"


# ---------------------------------------------------------------------------
# read_session tests
# ---------------------------------------------------------------------------


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


def test_read_session_from_vscdb(tmp_path):
	"""read_session reads directly from a .vscdb file when session_id is given."""
	db_path = tmp_path / "state.vscdb"
	_make_cursor_db(
		db_path,
		composers={"db1": {"composerId": "db1"}},
		bubbles=[
			("db1", "1", {"type": 1, "text": "hello user"}),
			("db1", "2", {"type": 2, "text": "hello assistant"}),
		],
	)
	session = read_session(db_path, session_id="db1")
	assert session is not None
	assert session.session_id == "db1"
	assert len(session.messages) == 2
	assert session.messages[0].role == "user"
	assert session.messages[1].role == "assistant"


def test_read_session_from_directory_containing_vscdb(tmp_path):
	"""read_session resolves a directory to state.vscdb inside it."""
	db_path = tmp_path / "state.vscdb"
	_make_cursor_db(
		db_path,
		composers={"dir1": {"composerId": "dir1"}},
		bubbles=[("dir1", "1", {"type": 1, "text": "msg"})],
	)
	session = read_session(tmp_path, session_id="dir1")
	assert session is not None
	assert session.session_id == "dir1"


def test_read_session_no_session_id_on_vscdb(tmp_path):
	"""read_session returns None for .vscdb path without session_id."""
	db_path = tmp_path / "state.vscdb"
	_make_cursor_db(db_path, composers={}, bubbles=[])
	assert read_session(db_path) is None


def test_read_session_nonexistent_jsonl(tmp_path):
	"""read_session returns None for a nonexistent .jsonl path."""
	fake = tmp_path / "nope.jsonl"
	assert read_session(fake) is None


def test_read_session_unknown_suffix(tmp_path):
	"""read_session returns None for a path with unknown suffix and no DB."""
	fake = tmp_path / "file.txt"
	fake.touch()
	assert read_session(fake) is None


# ---------------------------------------------------------------------------
# _read_session_db tests
# ---------------------------------------------------------------------------


def test_read_session_db_returns_viewer_session(tmp_path):
	"""_read_session_db returns a ViewerSession with correct messages."""
	db_path = tmp_path / "state.vscdb"
	_make_cursor_db(
		db_path,
		composers={"sid": {"composerId": "sid"}},
		bubbles=[
			("sid", "1", {"type": 1, "text": "user text"}),
			("sid", "2", {"type": 2, "text": "bot text"}),
			("sid", "3", {"type": 3, "text": "tool output"}),
		],
	)
	session = _read_session_db(db_path, "sid")
	assert session is not None
	assert session.session_id == "sid"
	assert len(session.messages) == 3
	assert session.messages[0].role == "user"
	assert session.messages[1].role == "assistant"
	assert session.messages[2].role == "tool"


def test_read_session_db_no_matching_bubbles(tmp_path):
	"""_read_session_db returns None when no bubbles match session_id."""
	db_path = tmp_path / "state.vscdb"
	_make_cursor_db(db_path, composers={}, bubbles=[])
	assert _read_session_db(db_path, "nonexistent") is None


def test_read_session_db_skips_empty_text(tmp_path):
	"""_read_session_db skips bubbles with empty text."""
	db_path = tmp_path / "state.vscdb"
	_make_cursor_db(
		db_path,
		composers={"e": {"composerId": "e"}},
		bubbles=[
			("e", "1", {"type": 1, "text": "has text"}),
			("e", "2", {"type": 2, "text": ""}),
			("e", "3", {"type": 2, "text": "   "}),
		],
	)
	session = _read_session_db(db_path, "e")
	assert session is not None
	assert len(session.messages) == 1


def test_read_session_db_non_dict_bubble_skipped(tmp_path):
	"""_read_session_db skips bubble values that parse to non-dict."""
	db_path = tmp_path / "state.vscdb"
	conn = sqlite3.connect(db_path)
	conn.execute("CREATE TABLE cursorDiskKV (key TEXT PRIMARY KEY, value TEXT)")
	conn.execute(
		"INSERT INTO cursorDiskKV VALUES (?, ?)",
		("bubbleId:s:1", json.dumps("just a string")),
	)
	conn.execute(
		"INSERT INTO cursorDiskKV VALUES (?, ?)",
		("bubbleId:s:2", json.dumps({"type": 1, "text": "real"})),
	)
	conn.commit()
	conn.close()
	session = _read_session_db(db_path, "s")
	assert session is not None
	assert len(session.messages) == 1


# ---------------------------------------------------------------------------
# find_session_path tests
# ---------------------------------------------------------------------------


def test_find_session_path_from_cache(tmp_path, monkeypatch):
	"""find_session_path returns cached JSONL when it exists."""
	cache_dir = tmp_path / "cache"
	cache_dir.mkdir()
	cached_file = cache_dir / "abc123.jsonl"
	cached_file.write_text("{}\n", encoding="utf-8")
	monkeypatch.setattr(
		"lerim.adapters.cursor._default_cache_dir", lambda: cache_dir
	)
	result = find_session_path("abc123")
	assert result == cached_file


def test_find_session_path_from_db(tmp_path, monkeypatch):
	"""find_session_path falls back to DB scan when cache miss."""
	cache_dir = tmp_path / "empty_cache"
	cache_dir.mkdir()
	monkeypatch.setattr(
		"lerim.adapters.cursor._default_cache_dir", lambda: cache_dir
	)
	db_path = tmp_path / "state.vscdb"
	_make_cursor_db(
		db_path,
		composers={},
		bubbles=[("mysess", "1", {"type": 1, "text": "hi"})],
	)
	result = find_session_path("mysess", traces_dir=tmp_path)
	assert result == db_path


def test_find_session_path_empty_id():
	"""find_session_path returns None for empty session_id."""
	assert find_session_path("") is None
	assert find_session_path("   ") is None


def test_find_session_path_not_found(tmp_path, monkeypatch):
	"""find_session_path returns None when session does not exist anywhere."""
	cache_dir = tmp_path / "cache"
	cache_dir.mkdir()
	monkeypatch.setattr(
		"lerim.adapters.cursor._default_cache_dir", lambda: cache_dir
	)
	db_path = tmp_path / "state.vscdb"
	_make_cursor_db(db_path, composers={}, bubbles=[])
	result = find_session_path("unknown", traces_dir=tmp_path)
	assert result is None


def test_find_session_path_nonexistent_root(tmp_path, monkeypatch):
	"""find_session_path returns None when traces_dir does not exist."""
	cache_dir = tmp_path / "cache"
	cache_dir.mkdir()
	monkeypatch.setattr(
		"lerim.adapters.cursor._default_cache_dir", lambda: cache_dir
	)
	result = find_session_path("abc", traces_dir=tmp_path / "nope")
	assert result is None


# ---------------------------------------------------------------------------
# exported_jsonl tests
# ---------------------------------------------------------------------------


def test_exported_jsonl_contains_compacted_data():
	"""Exported JSONL preserves non-empty fields after compaction."""
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


# --- compact_trace tests ---


def test_compact_trace_clears_tool_results():
	"""compact_trace replaces toolFormerData result with size descriptor."""
	bubble = {
		"type": 2,
		"text": "I ran the command",
		"toolFormerData": [
			{
				"name": "run_terminal_command_v2",
				"params": {"cmd": "ls"},
				"result": "x" * 5000,
				"status": "done",
			},
		],
	}
	result = compact_trace(json.dumps(bubble) + "\n")
	parsed = json.loads(result.strip())
	tool = parsed["toolFormerData"][0]
	assert tool["name"] == "run_terminal_command_v2"
	assert tool["params"] == {"cmd": "ls"}
	assert tool["result"] == "[cleared: 5000 chars]"
	assert tool["status"] == "done"


def test_compact_trace_clears_thinking_blocks():
	"""compact_trace replaces thinking text with size descriptor on capabilityType 30."""
	bubble = {
		"type": 2,
		"capabilityType": 30,
		"thinking": {"text": "y" * 10000, "signature": "sig123"},
		"text": "",
	}
	result = compact_trace(json.dumps(bubble) + "\n")
	parsed = json.loads(result.strip())
	assert parsed["thinking"]["text"] == "[thinking cleared: 10000 chars]"
	assert "signature" not in parsed["thinking"]


def test_compact_trace_strips_empty_fields():
	"""compact_trace removes fields with empty/falsy values."""
	bubble = {
		"type": 1,
		"text": "hello",
		"lints": [],
		"commits": [],
		"attachments": [],
		"extra": None,
	}
	result = compact_trace(json.dumps(bubble) + "\n")
	parsed = json.loads(result.strip())
	assert parsed["type"] == 1
	assert parsed["text"] == "hello"
	assert "lints" not in parsed
	assert "commits" not in parsed
	assert "attachments" not in parsed
	assert "extra" not in parsed


def test_compact_trace_preserves_user_assistant_text():
	"""compact_trace preserves text content of user and assistant bubbles."""
	lines = [
		json.dumps({"type": 1, "text": "user message"}),
		json.dumps({"type": 2, "text": "assistant reply"}),
	]
	result = compact_trace("\n".join(lines) + "\n")
	parsed = [json.loads(line) for line in result.strip().split("\n")]
	assert parsed[0]["text"] == "user message"
	assert parsed[1]["text"] == "assistant reply"


def test_compact_trace_preserves_false_and_zero():
	"""compact_trace keeps False and 0 values (only strips empty containers and None)."""
	bubble = {
		"type": 1,
		"text": "msg",
		"enabled": False,
		"count": 0,
	}
	result = compact_trace(json.dumps(bubble) + "\n")
	parsed = json.loads(result.strip())
	assert parsed["enabled"] is False
	assert parsed["count"] == 0


def test_compact_trace_non_dict_tool_former_data_entry():
	"""compact_trace handles non-dict entries in toolFormerData gracefully."""
	bubble = {
		"type": 2,
		"text": "mixed",
		"toolFormerData": [
			"not a dict",
			{"name": "tool", "result": "data"},
		],
	}
	result = compact_trace(json.dumps(bubble) + "\n")
	parsed = json.loads(result.strip())
	tools = parsed["toolFormerData"]
	assert tools[0] == "not a dict"
	assert tools[1]["result"] == "[cleared: 4 chars]"


# ---------------------------------------------------------------------------
# Time window filtering tests
# ---------------------------------------------------------------------------


def test_iter_sessions_time_window_filter(tmp_path):
	"""iter_sessions filters sessions outside the start/end time window."""
	from datetime import datetime, timezone

	db_path = tmp_path / "state.vscdb"
	cache_dir = tmp_path / "cache"
	_make_cursor_db(
		db_path,
		composers={
			"old": {"composerId": "old", "createdAt": 1600000000000},
			"new": {"composerId": "new", "createdAt": 1800000000000},
		},
		bubbles=[
			("old", "1", {"type": 1, "text": "old msg"}),
			("new", "1", {"type": 1, "text": "new msg"}),
		],
	)
	# Window that only includes the "new" session
	start = datetime(2025, 1, 1, tzinfo=timezone.utc)
	records = iter_sessions(traces_dir=tmp_path, cache_dir=cache_dir, start=start)
	assert len(records) == 1
	assert records[0].run_id == "new"


def test_iter_sessions_empty_text_not_in_summaries(tmp_path):
	"""Bubbles with empty text after stripping are not added to summaries."""
	db_path = tmp_path / "state.vscdb"
	cache_dir = tmp_path / "cache"
	_make_cursor_db(
		db_path,
		composers={"es": {"composerId": "es", "createdAt": 1700000000000}},
		bubbles=[
			("es", "1", {"type": 1, "text": "   "}),
			("es", "2", {"type": 1, "text": "real text"}),
		],
	)
	records = iter_sessions(traces_dir=tmp_path, cache_dir=cache_dir)
	assert len(records) == 1
	# Only "real text" should appear (empty stripped text is skipped)
	assert records[0].summaries == ["real text"]


def test_read_session_jsonl_fallback_id(tmp_path):
	"""read_session falls back to composerId from metadata when no session_id given."""
	cache_dir = tmp_path / "cache"
	cache_dir.mkdir()
	jsonl = cache_dir / "test.jsonl"
	lines = [
		json.dumps({"composerId": "from_meta"}),
		json.dumps({"type": 1, "text": "user msg"}),
	]
	jsonl.write_text("\n".join(lines) + "\n", encoding="utf-8")
	session = read_session(jsonl)
	assert session is not None
	assert session.session_id == "from_meta"


def test_read_session_jsonl_stem_fallback_id(tmp_path):
	"""read_session uses path stem when no session_id and no composerId in metadata."""
	cache_dir = tmp_path / "cache"
	cache_dir.mkdir()
	jsonl = cache_dir / "my_session.jsonl"
	lines = [
		json.dumps({"some_key": "some_val"}),
		json.dumps({"type": 1, "text": "msg"}),
	]
	jsonl.write_text("\n".join(lines) + "\n", encoding="utf-8")
	session = read_session(jsonl)
	assert session is not None
	assert session.session_id == "my_session"


def test_read_session_jsonl_strips_empty_text_bubbles(tmp_path):
	"""read_session from JSONL skips bubbles with empty text."""
	cache_dir = tmp_path / "cache"
	cache_dir.mkdir()
	jsonl = cache_dir / "strip.jsonl"
	lines = [
		json.dumps({"composerId": "st"}),
		json.dumps({"type": 1, "text": "   "}),
		json.dumps({"type": 2, "text": "real reply"}),
	]
	jsonl.write_text("\n".join(lines) + "\n", encoding="utf-8")
	session = read_session(jsonl, "st")
	assert session is not None
	assert len(session.messages) == 1
	assert session.messages[0].role == "assistant"


# ---------------------------------------------------------------------------
# SQLite error handling tests
# ---------------------------------------------------------------------------


def test_validate_connection_sqlite_error(tmp_path):
	"""validate_connection returns error on corrupt DB."""
	db_path = tmp_path / "state.vscdb"
	db_path.write_bytes(b"this is not a sqlite database")
	result = validate_connection(tmp_path)
	assert result["ok"] is False
	assert "error" in result


def test_count_sessions_sqlite_error(tmp_path):
	"""count_sessions returns 0 on corrupt DB."""
	db_path = tmp_path / "state.vscdb"
	# Create a valid DB then corrupt it by overwriting with bad data
	_make_cursor_db(db_path, composers={}, bubbles=[])
	# Corrupt the table
	conn = sqlite3.connect(db_path)
	conn.execute("DROP TABLE cursorDiskKV")
	conn.execute("CREATE TABLE cursorDiskKV (key INTEGER)")
	conn.commit()
	conn.close()
	# count_sessions tries SELECT key FROM cursorDiskKV WHERE key LIKE ... which will fail on split
	# But it won't raise sqlite3.Error. Let's use a truly corrupt DB approach.
	db_path.unlink()
	db_path.write_bytes(b"not a database at all")
	assert count_sessions(tmp_path) == 0


def test_iter_sessions_sqlite_error(tmp_path):
	"""iter_sessions returns empty list on corrupt DB."""
	db_path = tmp_path / "state.vscdb"
	db_path.write_bytes(b"corrupt sqlite data")
	records = iter_sessions(traces_dir=tmp_path, cache_dir=tmp_path / "cache")
	assert records == []


def test_find_session_path_sqlite_error(tmp_path, monkeypatch):
	"""find_session_path returns None on SQLite error during DB scan."""
	cache_dir = tmp_path / "cache"
	cache_dir.mkdir()
	monkeypatch.setattr(
		"lerim.adapters.cursor._default_cache_dir", lambda: cache_dir
	)
	db_path = tmp_path / "state.vscdb"
	db_path.write_bytes(b"corrupt data")
	result = find_session_path("xyz", traces_dir=tmp_path)
	assert result is None


def test_read_session_db_sqlite_error(tmp_path):
	"""_read_session_db returns None on SQLite error."""
	db_path = tmp_path / "state.vscdb"
	db_path.write_bytes(b"corrupt data")
	assert _read_session_db(db_path, "sid") is None


def test_read_session_empty_jsonl(tmp_path):
	"""read_session returns None for a JSONL with no valid dict lines."""
	jsonl = tmp_path / "empty.jsonl"
	jsonl.write_text("\n\n", encoding="utf-8")
	assert read_session(jsonl) is None
