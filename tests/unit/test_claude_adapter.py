"""Unit tests for the Claude session adapter."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from lerim.adapters.claude import (
    compact_trace,
    count_sessions,
    default_path,
    find_session_path,
    iter_sessions,
    read_session,
)

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "traces"


def _write_claude_jsonl(path: Path, entries: list[dict]) -> Path:
    """Write Claude-format JSONL entries to a file."""
    with path.open("w", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(json.dumps(entry) + "\n")
    return path


def test_read_session_parses_user_messages(tmp_path):
    """Claude JSONL with human type -> ViewerMessage(role=user)."""
    f = _write_claude_jsonl(
        tmp_path / "sess.jsonl",
        [
            {
                "type": "user",
                "message": {"content": "Hello world"},
                "timestamp": "2026-02-20T10:00:00Z",
            },
        ],
    )
    session = read_session(f, "sess")
    assert session is not None
    user_msgs = [m for m in session.messages if m.role == "user"]
    assert len(user_msgs) == 1
    assert user_msgs[0].content == "Hello world"


def test_read_session_parses_assistant_messages(tmp_path):
    """Claude JSONL with assistant type -> ViewerMessage(role=assistant)."""
    f = _write_claude_jsonl(
        tmp_path / "sess.jsonl",
        [
            {
                "type": "assistant",
                "message": {
                    "content": [{"type": "text", "text": "Reply here"}],
                    "model": "claude-4",
                },
                "timestamp": "2026-02-20T10:00:05Z",
            },
        ],
    )
    session = read_session(f, "sess")
    assert session is not None
    asst_msgs = [m for m in session.messages if m.role == "assistant"]
    assert len(asst_msgs) == 1
    assert "Reply here" in asst_msgs[0].content


def test_read_session_parses_tool_use(tmp_path):
    """Claude JSONL with tool_use blocks -> ViewerMessage with tool_name."""
    f = _write_claude_jsonl(
        tmp_path / "sess.jsonl",
        [
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "t1",
                            "name": "read_file",
                            "input": {"path": "/tmp"},
                        },
                    ]
                },
                "timestamp": "2026-02-20T10:00:05Z",
            },
        ],
    )
    session = read_session(f, "sess")
    assert session is not None
    tool_msgs = [m for m in session.messages if m.role == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0].tool_name == "read_file"


def test_read_session_token_counting(tmp_path):
    """Token fields accumulate into ViewerSession totals."""
    f = _write_claude_jsonl(
        tmp_path / "sess.jsonl",
        [
            {
                "type": "assistant",
                "message": {
                    "content": [{"type": "text", "text": "a"}],
                    "usage": {"input_tokens": 100, "output_tokens": 50},
                },
            },
            {
                "type": "assistant",
                "message": {
                    "content": [{"type": "text", "text": "b"}],
                    "usage": {"input_tokens": 200, "output_tokens": 75},
                },
            },
        ],
    )
    session = read_session(f, "sess")
    assert session is not None
    assert session.total_input_tokens == 300
    assert session.total_output_tokens == 125


def test_read_session_timestamp_extraction(tmp_path):
    """First message timestamp is preserved."""
    f = _write_claude_jsonl(
        tmp_path / "sess.jsonl",
        [
            {
                "type": "user",
                "message": {"content": "Hello"},
                "timestamp": "2026-02-20T10:00:00Z",
            },
        ],
    )
    session = read_session(f, "sess")
    assert session is not None
    assert session.messages[0].timestamp == "2026-02-20T10:00:00Z"


def test_iter_sessions_window_filtering(tmp_path):
    """iter_sessions with start/end window only returns sessions within range."""
    _write_claude_jsonl(
        tmp_path / "early.jsonl",
        [
            {
                "type": "user",
                "message": {"content": "hi"},
                "timestamp": "2026-01-01T10:00:00Z",
            },
        ],
    )
    _write_claude_jsonl(
        tmp_path / "late.jsonl",
        [
            {
                "type": "user",
                "message": {"content": "hi"},
                "timestamp": "2026-03-01T10:00:00Z",
            },
        ],
    )
    start = datetime(2026, 2, 1, tzinfo=timezone.utc)
    end = datetime(2026, 2, 28, tzinfo=timezone.utc)
    records = iter_sessions(traces_dir=tmp_path, start=start, end=end)
    assert len(records) == 0  # both outside Feb range


def test_iter_sessions_skips_known_ids(tmp_path):
    """iter_sessions skips sessions whose run_id is already known."""
    _write_claude_jsonl(
        tmp_path / "known.jsonl",
        [
            {
                "type": "user",
                "message": {"content": "hi"},
                "timestamp": "2026-02-20T10:00:00Z",
            },
        ],
    )
    _write_claude_jsonl(
        tmp_path / "new.jsonl",
        [
            {
                "type": "user",
                "message": {"content": "hi"},
                "timestamp": "2026-02-20T10:00:00Z",
            },
        ],
    )
    # Skip "known" by providing its ID
    records = iter_sessions(
        traces_dir=tmp_path,
        known_run_ids={"known"},
    )
    assert len(records) == 1
    assert records[0].run_id == "new"


def test_read_session_empty_file(tmp_path):
    """Empty JSONL file -> ViewerSession with zero messages."""
    f = tmp_path / "empty.jsonl"
    f.write_text("", encoding="utf-8")
    session = read_session(f, "empty")
    assert session is not None
    assert len(session.messages) == 0


def test_read_session_malformed_lines(tmp_path):
    """JSONL with some invalid JSON lines -> skips bad lines, parses good ones."""
    f = tmp_path / "bad.jsonl"
    f.write_text(
        'not-json\n{"type":"user","message":{"content":"good"}}\n{broken\n',
        encoding="utf-8",
    )
    session = read_session(f, "bad")
    assert session is not None
    user_msgs = [m for m in session.messages if m.role == "user"]
    assert len(user_msgs) == 1


def test_count_sessions(tmp_path):
    """count_sessions counts non-empty JSONL files in directory."""
    (tmp_path / "a.jsonl").write_text('{"x":1}\n', encoding="utf-8")
    (tmp_path / "b.jsonl").write_text('{"x":2}\n', encoding="utf-8")
    (tmp_path / "empty.jsonl").write_text("", encoding="utf-8")
    assert count_sessions(tmp_path) == 2


def test_find_session_path(tmp_path):
    """find_session_path locates file by session_id stem."""
    target = tmp_path / "my-session.jsonl"
    target.write_text('{"x":1}\n', encoding="utf-8")
    found = find_session_path("my-session", traces_dir=tmp_path)
    assert found is not None
    assert found.name == "my-session.jsonl"


def test_default_path():
    """default_path returns ~/.claude/projects/."""
    result = default_path()
    assert result is not None
    assert str(result).endswith(".claude/projects")


# --- compact_trace tests ---


def test_compact_trace_drops_noise_types():
    """compact_trace drops progress/file-history-snapshot/queue-operation/pr-link lines."""
    lines = [
        json.dumps({"type": "progress", "data": "loading"}),
        json.dumps({"type": "user", "message": {"content": "hi"}, "timestamp": "t1"}),
        json.dumps({"type": "file-history-snapshot", "files": []}),
        json.dumps({"type": "queue-operation", "op": "enqueue"}),
        json.dumps({"type": "pr-link", "url": "https://example.com"}),
        json.dumps(
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "hello"}]},
                "timestamp": "t2",
            }
        ),
    ]
    result = compact_trace("\n".join(lines) + "\n")
    parsed = [json.loads(line) for line in result.strip().split("\n")]
    assert len(parsed) == 2
    assert parsed[0]["type"] == "user"
    assert parsed[1]["type"] == "assistant"


def test_compact_trace_strips_metadata_fields():
    """compact_trace keeps only type/message/timestamp, strips everything else."""
    entry = {
        "type": "user",
        "message": {"content": "hello"},
        "timestamp": "2026-01-01T00:00:00Z",
        "parentUuid": "abc",
        "isSidechain": False,
        "userType": "external",
        "cwd": "/home/user",
        "sessionId": "s1",
        "version": "1.0",
        "gitBranch": "main",
        "slug": "test",
        "uuid": "xyz",
        "requestId": "r1",
        "toolUseResult": "x" * 5_000_000,
        "planContent": "some plan",
        "sourceToolAssistantUUID": "a1",
    }
    result = compact_trace(json.dumps(entry) + "\n")
    parsed = json.loads(result.strip())
    assert set(parsed.keys()) == {"type", "message", "timestamp"}
    assert parsed["message"]["content"] == "hello"


def test_compact_trace_clears_tool_result_string():
    """compact_trace replaces tool_result string content with size descriptor."""
    big_content = "x" * 100_000
    entry = {
        "type": "user",
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "t1",
                    "content": big_content,
                }
            ]
        },
        "timestamp": "t1",
    }
    result = compact_trace(json.dumps(entry) + "\n")
    parsed = json.loads(result.strip())
    inner = parsed["message"]["content"][0]["content"]
    assert inner == "[cleared: 100000 chars]"


def test_compact_trace_clears_tool_result_list():
    """compact_trace replaces tool_result list content with size descriptor."""
    entry = {
        "type": "user",
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "t1",
                    "content": [
                        {"type": "text", "text": "a" * 40_000},
                        {"type": "text", "text": "b" * 40_000},
                    ],
                }
            ]
        },
        "timestamp": "t1",
    }
    result = compact_trace(json.dumps(entry) + "\n")
    parsed = json.loads(result.strip())
    inner = parsed["message"]["content"][0]["content"]
    assert inner == "[cleared: 80000 chars]"


def test_compact_trace_clears_small_tool_results():
    """compact_trace clears ALL tool_result content regardless of size."""
    small_content = "result data"
    entry = {
        "type": "user",
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "t1",
                    "content": small_content,
                }
            ]
        },
        "timestamp": "t1",
    }
    result = compact_trace(json.dumps(entry) + "\n")
    parsed = json.loads(result.strip())
    assert (
        parsed["message"]["content"][0]["content"]
        == f"[cleared: {len(small_content)} chars]"
    )


def test_compact_trace_clears_thinking_blocks():
    """compact_trace replaces thinking block text with size descriptor and drops signature."""
    entry = {
        "type": "assistant",
        "message": {
            "content": [
                {"type": "thinking", "thinking": "x" * 5000, "signature": "abc123"},
                {"type": "text", "text": "My conclusion"},
            ]
        },
        "timestamp": "t1",
    }
    result = compact_trace(json.dumps(entry) + "\n")
    parsed = json.loads(result.strip())
    thinking_block = parsed["message"]["content"][0]
    assert thinking_block["thinking"] == "[thinking cleared: 5000 chars]"
    assert "signature" not in thinking_block
    text_block = parsed["message"]["content"][1]
    assert text_block["text"] == "My conclusion"


def test_compact_trace_keeps_malformed_lines():
    """compact_trace preserves non-JSON lines as-is."""
    raw = (
        "not-json\n"
        + json.dumps({"type": "user", "message": {"content": "hi"}, "timestamp": "t"})
        + "\n"
    )
    result = compact_trace(raw)
    lines = [line for line in result.strip().split("\n") if line.strip()]
    assert lines[0] == "not-json"
    assert json.loads(lines[1])["type"] == "user"
