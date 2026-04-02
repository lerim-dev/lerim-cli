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


# --- _extract_message_text edge cases ---


def test_extract_message_text_none():
    """None content returns None."""
    assert _extract_message_text(None) is None


def test_extract_message_text_integer():
    """Integer content returns None (not str or list)."""
    assert _extract_message_text(42) is None


def test_extract_message_text_empty_list():
    """Empty list content returns None."""
    assert _extract_message_text([]) is None


def test_extract_message_text_list_no_text_keys():
    """List of dicts without 'text' keys returns None."""
    content = [{"type": "image", "url": "http://example.com"}]
    assert _extract_message_text(content) is None


def test_extract_message_text_list_mixed():
    """List with mix of dict-with-text and dict-without-text extracts text only."""
    content = [
        {"type": "image", "url": "http://example.com"},
        {"text": "visible text"},
        {"type": "code", "code": "x=1"},
    ]
    result = _extract_message_text(content)
    assert result == "visible text"


def test_extract_message_text_list_with_non_string_text():
    """List entries where text is not a string are skipped."""
    content = [{"text": 123}, {"text": "ok"}]
    result = _extract_message_text(content)
    assert result == "ok"


# --- iter_sessions edge cases ---


def test_iter_sessions_empty_dir(tmp_path):
    """Empty directory returns empty list."""
    records = iter_sessions(traces_dir=tmp_path)
    assert records == []


def test_iter_sessions_nonexistent_dir():
    """Non-existent directory returns empty list."""
    from pathlib import Path

    records = iter_sessions(traces_dir=Path("/tmp/nonexistent_codex_dir_abc"))
    assert records == []


def test_iter_sessions_skips_empty_jsonl(tmp_path):
    """JSONL file with no valid entries is skipped."""
    (tmp_path / "empty.jsonl").write_text("", encoding="utf-8")
    records = iter_sessions(traces_dir=tmp_path)
    assert records == []


def test_iter_sessions_extracts_metadata(tmp_path):
    """iter_sessions extracts repo metadata from session_meta entries."""
    _write_codex_jsonl(
        tmp_path / "sess1.jsonl",
        [
            {
                "type": "session_meta",
                "timestamp": "2026-03-20T10:00:00Z",
                "payload": {
                    "cwd": "/home/user/projects/myrepo",
                    "git": {"branch": "main"},
                },
            },
            {
                "type": "event_msg",
                "timestamp": "2026-03-20T10:00:01Z",
                "payload": {"type": "user_message", "message": "hello"},
            },
        ],
    )
    records = iter_sessions(traces_dir=tmp_path)
    assert len(records) == 1
    assert records[0].repo_path == "/home/user/projects/myrepo"
    assert records[0].repo_name == "main"
    assert records[0].message_count == 1


def test_iter_sessions_counts_tokens(tmp_path):
    """iter_sessions accumulates token counts from token_count events."""
    _write_codex_jsonl(
        tmp_path / "tok.jsonl",
        [
            {
                "type": "event_msg",
                "payload": {"type": "user_message", "message": "hi"},
            },
            {
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "last_token_usage": {
                            "input_tokens": 100,
                            "output_tokens": 50,
                            "reasoning_output_tokens": 20,
                        }
                    },
                },
            },
        ],
    )
    records = iter_sessions(traces_dir=tmp_path)
    assert len(records) == 1
    assert records[0].total_tokens == 170


def test_iter_sessions_counts_tool_calls_and_errors(tmp_path):
    """iter_sessions counts function calls and errors in output."""
    _write_codex_jsonl(
        tmp_path / "tools.jsonl",
        [
            {
                "type": "event_msg",
                "payload": {"type": "user_message", "message": "do it"},
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "read_file",
                    "arguments": "{}",
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call_output",
                    "output": "Error: file not found",
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call",
                    "name": "write_file",
                    "input": "{}",
                },
            },
        ],
    )
    records = iter_sessions(traces_dir=tmp_path)
    assert len(records) == 1
    assert records[0].tool_call_count == 2
    assert records[0].error_count == 1


# --- read_session edge cases ---


def test_read_session_empty_file(tmp_path):
    """Empty JSONL file produces session with no messages."""
    f = tmp_path / "empty.jsonl"
    f.write_text("", encoding="utf-8")
    session = read_session(f, "empty")
    assert session is not None
    assert session.messages == []


def test_read_session_function_call_output_unknown_id(tmp_path):
    """function_call_output with unknown call_id becomes standalone tool message."""
    f = _write_codex_jsonl(
        tmp_path / "orphan.jsonl",
        [
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call_output",
                    "call_id": "unknown-123",
                    "output": "some result",
                },
            },
        ],
    )
    session = read_session(f, "orphan")
    assert session is not None
    assert len(session.messages) == 1
    assert session.messages[0].role == "tool"
    assert session.messages[0].tool_output == "some result"


def test_read_session_custom_tool_call(tmp_path):
    """custom_tool_call entries are parsed as tool messages."""
    f = _write_codex_jsonl(
        tmp_path / "custom.jsonl",
        [
            {
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call",
                    "id": "ct-1",
                    "name": "my_tool",
                    "input": {"arg": "val"},
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call_output",
                    "id": "ct-1",
                    "output": "tool result",
                },
            },
        ],
    )
    session = read_session(f, "custom")
    assert session is not None
    tool_msgs = [m for m in session.messages if m.role == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0].tool_name == "my_tool"
    assert tool_msgs[0].tool_output == "tool result"


def test_read_session_token_counting(tmp_path):
    """token_count events accumulate input/output tokens."""
    f = _write_codex_jsonl(
        tmp_path / "tokens.jsonl",
        [
            {
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "last_token_usage": {
                            "input_tokens": 200,
                            "output_tokens": 100,
                            "reasoning_output_tokens": 50,
                        }
                    },
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": "hi",
                },
            },
        ],
    )
    session = read_session(f, "tokens")
    assert session is not None
    assert session.total_input_tokens == 200
    assert session.total_output_tokens == 150  # 100 + 50


def test_read_session_uses_stem_as_default_id(tmp_path):
    """When session_id is None, the file stem is used."""
    f = _write_codex_jsonl(
        tmp_path / "my-session.jsonl",
        [
            {
                "type": "response_item",
                "payload": {"type": "message", "role": "assistant", "content": "hi"},
            },
        ],
    )
    session = read_session(f)
    assert session is not None
    assert session.session_id == "my-session"


# --- compact_trace / _clean_entry edge cases ---


def test_compact_trace_no_payload():
    """Entry without payload key is kept as-is."""
    entry = {"type": "unknown_type", "data": "something"}
    result = compact_trace(json.dumps(entry) + "\n")
    parsed = json.loads(result.strip())
    assert parsed == entry


def test_compact_trace_reasoning_non_list_content():
    """Reasoning with non-list content uses str length."""
    entry = {
        "type": "response_item",
        "payload": {
            "type": "reasoning",
            "content": "plain string reasoning content",
        },
    }
    result = compact_trace(json.dumps(entry) + "\n")
    parsed = json.loads(result.strip())
    assert "reasoning cleared:" in parsed["payload"]["content"]
    assert "30 chars" in parsed["payload"]["content"]


def test_compact_trace_multiple_lines():
    """compact_trace processes multiple JSONL lines correctly."""
    lines = [
        json.dumps({"type": "turn_context", "payload": {}}),
        json.dumps({"type": "session_meta", "payload": {"id": "s1", "base_instructions": "long"}}),
        json.dumps({"type": "event_msg", "payload": {"type": "user_message", "message": "hi"}}),
    ]
    result = compact_trace("\n".join(lines) + "\n")
    parsed = [json.loads(line) for line in result.strip().split("\n")]
    # turn_context is dropped -> 2 lines remain
    assert len(parsed) == 2
    # base_instructions is stripped from session_meta
    assert "base_instructions" not in parsed[0]["payload"]


# --- find_session_path edge cases ---


def test_find_session_path_empty_id(tmp_path):
    """Empty session_id returns None."""
    (tmp_path / "test.jsonl").write_text("{}\n", encoding="utf-8")
    assert find_session_path("", traces_dir=tmp_path) is None


def test_find_session_path_whitespace_id(tmp_path):
    """Whitespace-only session_id returns None."""
    (tmp_path / "test.jsonl").write_text("{}\n", encoding="utf-8")
    assert find_session_path("   ", traces_dir=tmp_path) is None


def test_find_session_path_nonexistent_dir():
    """Non-existent traces_dir returns None."""
    from pathlib import Path

    result = find_session_path("any-id", traces_dir=Path("/tmp/nonexistent_abc_123"))
    assert result is None
