"""Unit tests for the Codex session adapter."""

from __future__ import annotations

import json
from pathlib import Path

from lerim.adapters.codex import (
    _extract_message_text,
    compact_trace,
    count_sessions,
    find_session_path,
    iter_sessions,
    read_session,
)


def _write_codex_jsonl(path: Path, entries: list[dict]) -> Path:
    """Write Codex-format JSONL entries to a file."""
    with path.open("w", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(json.dumps(entry) + "\n")
    return path


def test_read_session_response_item_format(tmp_path):
    """Codex JSONL with response_item events -> ViewerMessages."""
    f = _write_codex_jsonl(
        tmp_path / "sess.jsonl",
        [
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "Hello from codex"}],
                },
            },
        ],
    )
    session = read_session(f, "sess")
    assert session is not None
    asst = [m for m in session.messages if m.role == "assistant"]
    assert len(asst) == 1
    assert "Hello from codex" in asst[0].content


def test_read_session_event_msg_format(tmp_path):
    """Codex JSONL with event_msg/user_message events -> ViewerMessages."""
    f = _write_codex_jsonl(
        tmp_path / "sess.jsonl",
        [
            {
                "type": "event_msg",
                "payload": {"type": "user_message", "message": "User says hi"},
            },
            {
                "type": "event_msg",
                "payload": {"type": "agent_message", "message": "Agent replies"},
            },
        ],
    )
    session = read_session(f, "sess")
    assert session is not None
    assert len(session.messages) == 2
    assert session.messages[0].role == "user"
    assert session.messages[1].role == "assistant"


def test_extract_message_text_string():
    """String content -> returned as-is."""
    assert _extract_message_text("hello") == "hello"


def test_extract_message_text_list():
    """List content with text items -> concatenated."""
    content = [{"text": "line1"}, {"text": "line2"}]
    result = _extract_message_text(content)
    assert result is not None
    assert "line1" in result
    assert "line2" in result


def test_iter_sessions_enumeration(tmp_path):
    """iter_sessions returns SessionRecords for all JSONL files."""
    _write_codex_jsonl(
        tmp_path / "a.jsonl",
        [
            {"type": "event_msg", "payload": {"type": "user_message", "message": "hi"}},
        ],
    )
    _write_codex_jsonl(
        tmp_path / "b.jsonl",
        [
            {
                "type": "event_msg",
                "payload": {"type": "user_message", "message": "hello"},
            },
        ],
    )
    records = iter_sessions(traces_dir=tmp_path)
    assert len(records) == 2
    run_ids = {r.run_id for r in records}
    assert "a" in run_ids
    assert "b" in run_ids


def test_count_sessions(tmp_path):
    """count_sessions counts non-empty files."""
    (tmp_path / "a.jsonl").write_text('{"x":1}\n', encoding="utf-8")
    (tmp_path / "empty.jsonl").write_text("", encoding="utf-8")
    assert count_sessions(tmp_path) == 1


def test_find_session_path_exact_and_partial(tmp_path):
    """find_session_path with exact stem and partial match."""
    target = tmp_path / "my-session-123.jsonl"
    target.write_text('{"x":1}\n', encoding="utf-8")
    # Exact match
    found = find_session_path("my-session-123", traces_dir=tmp_path)
    assert found is not None
    # Partial match
    found_partial = find_session_path("session-123", traces_dir=tmp_path)
    assert found_partial is not None


def test_iter_sessions_skips_known_ids(tmp_path):
    """iter_sessions skips sessions whose run_id is already known."""
    _write_codex_jsonl(
        tmp_path / "stable.jsonl",
        [{"type": "event_msg", "payload": {"type": "user_message", "message": "hi"}}],
    )
    _write_codex_jsonl(
        tmp_path / "new.jsonl",
        [
            {
                "type": "event_msg",
                "payload": {"type": "user_message", "message": "hello"},
            }
        ],
    )
    # Skip "stable" by providing its ID
    records = iter_sessions(
        traces_dir=tmp_path,
        known_run_ids={"stable"},
    )
    assert len(records) == 1
    assert records[0].run_id == "new"


# --- compact_trace tests ---


def test_compact_trace_drops_turn_context():
    """compact_trace drops turn_context lines."""
    lines = [
        json.dumps({"type": "turn_context", "payload": {"files": ["a.py"]}}),
        json.dumps(
            {"type": "event_msg", "payload": {"type": "user_message", "message": "hi"}}
        ),
    ]
    result = compact_trace("\n".join(lines) + "\n")
    parsed = [json.loads(line) for line in result.strip().split("\n")]
    assert len(parsed) == 1
    assert parsed[0]["type"] == "event_msg"


def test_compact_trace_strips_base_instructions():
    """compact_trace removes base_instructions from session_meta."""
    entry = {
        "type": "session_meta",
        "payload": {"id": "s1", "cwd": "/tmp", "base_instructions": "x" * 10_000},
    }
    result = compact_trace(json.dumps(entry) + "\n")
    parsed = json.loads(result.strip())
    assert "base_instructions" not in parsed["payload"]
    assert parsed["payload"]["id"] == "s1"


def test_compact_trace_clears_function_call_output():
    """compact_trace replaces function_call_output content with size descriptor."""
    entry = {
        "type": "response_item",
        "payload": {"type": "function_call_output", "output": "x" * 50_000},
    }
    result = compact_trace(json.dumps(entry) + "\n")
    parsed = json.loads(result.strip())
    assert parsed["payload"]["output"] == "[cleared: 50000 chars]"


def test_compact_trace_clears_reasoning():
    """compact_trace replaces reasoning content with size descriptor."""
    entry = {
        "type": "response_item",
        "payload": {
            "type": "reasoning",
            "content": [{"type": "text", "text": "y" * 8000}],
        },
    }
    result = compact_trace(json.dumps(entry) + "\n")
    parsed = json.loads(result.strip())
    assert parsed["payload"]["content"] == "[reasoning cleared: 8000 chars]"


def test_compact_trace_clears_agent_reasoning():
    """compact_trace replaces agent_reasoning event message with size descriptor."""
    entry = {
        "type": "event_msg",
        "payload": {"type": "agent_reasoning", "message": "z" * 3000},
    }
    result = compact_trace(json.dumps(entry) + "\n")
    parsed = json.loads(result.strip())
    assert parsed["payload"]["message"] == "[reasoning cleared: 3000 chars]"


def test_compact_trace_preserves_function_call():
    """compact_trace keeps function_call name and arguments intact."""
    entry = {
        "type": "response_item",
        "payload": {
            "type": "function_call",
            "name": "read_file",
            "arguments": '{"path": "/tmp/x.py"}',
        },
    }
    result = compact_trace(json.dumps(entry) + "\n")
    parsed = json.loads(result.strip())
    assert parsed["payload"]["name"] == "read_file"
    assert parsed["payload"]["arguments"] == '{"path": "/tmp/x.py"}'
