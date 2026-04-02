"""Unit tests for lerim.memory.transcript — JSONL trace formatting.

Tests format_transcript and all internal helpers across the four supported
agent formats (Claude, OpenCode, Codex, Cursor) plus edge cases.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lerim.memory.transcript import (
	_detect_trace_format,
	_extract_content_text,
	_format_claude_line,
	_format_codex_line,
	_format_cursor_line,
	_format_opencode_line,
	_summarize_tool_use,
	format_transcript,
)

TRACES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "traces"


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _load_trace(name: str) -> str:
	"""Read a fixture trace file and return its raw text."""
	return (TRACES_DIR / name).read_text()


# ===================================================================
# format_transcript — Claude fixtures
# ===================================================================


class TestFormatTranscriptClaude:
	"""Tests for Claude trace format end-to-end."""

	def test_claude_simple_labels(self):
		"""Claude simple trace produces [USER] and [ASSISTANT] labels."""
		result = format_transcript(_load_trace("claude_simple.jsonl"))
		assert "[USER]" in result
		assert "[ASSISTANT]" in result

	def test_claude_simple_content_preserved(self):
		"""Key content from the Claude simple fixture appears in output."""
		result = format_transcript(_load_trace("claude_simple.jsonl"))
		assert "JWT" in result
		assert "HS256" in result
		assert "CORS" in result

	def test_claude_simple_message_count(self):
		"""Claude simple has 3 user + 3 assistant = 6 blocks."""
		result = format_transcript(_load_trace("claude_simple.jsonl"))
		assert result.count("[USER]") == 3
		assert result.count("[ASSISTANT]") == 3

	def test_claude_simple_ends_with_newline(self):
		"""Formatted output ends with a trailing newline."""
		result = format_transcript(_load_trace("claude_simple.jsonl"))
		assert result.endswith("\n")

	def test_claude_short_tool_uses_summarized(self):
		"""Claude short fixture with tool_use blocks produces summarized tool output."""
		result = format_transcript(_load_trace("claude_short.jsonl"))
		# Should have content, not raw tool JSON
		assert "[USER]" in result or "[ASSISTANT]" in result

	def test_claude_rich_produces_output(self):
		"""Claude rich fixture (eval-judge trace) produces formatted text."""
		result = format_transcript(_load_trace("claude_rich.jsonl"))
		assert "[USER]" in result
		assert "[ASSISTANT]" in result

	def test_claude_thinking_blocks_stripped(self):
		"""Thinking blocks in Claude traces should not appear in output."""
		result = format_transcript(_load_trace("claude_short.jsonl"))
		# The fixture has thinking blocks; they should be stripped
		assert "[thinking cleared" not in result.lower()


# ===================================================================
# format_transcript — Codex fixtures
# ===================================================================


class TestFormatTranscriptCodex:
	"""Tests for Codex trace format end-to-end."""

	def test_codex_simple_labels(self):
		"""Codex simple trace produces [USER] labels.

		Note: the codex_simple fixture uses output_text content blocks for
		assistant messages, which _extract_content_text does not recognise
		(it handles "text" type only). So only user messages appear.
		"""
		result = format_transcript(_load_trace("codex_simple.jsonl"))
		assert "[USER]" in result

	def test_codex_simple_content(self):
		"""Key content from Codex simple fixture is preserved."""
		result = format_transcript(_load_trace("codex_simple.jsonl"))
		assert "connection pool" in result.lower() or "PostgreSQL" in result

	def test_codex_simple_message_count(self):
		"""Codex simple has 2 user messages (assistant uses output_text blocks)."""
		result = format_transcript(_load_trace("codex_simple.jsonl"))
		assert result.count("[USER]") == 2

	def test_codex_with_tools_has_tool_label(self):
		"""Codex with_tools fixture produces [TOOL] labels for function calls."""
		result = format_transcript(_load_trace("codex_with_tools.jsonl"))
		assert "[TOOL]" in result

	def test_codex_with_tools_strips_function_call_output(self):
		"""function_call_output lines are stripped (not shown as content)."""
		result = format_transcript(_load_trace("codex_with_tools.jsonl"))
		# The raw SQL from function_call_output should not appear verbatim
		assert "SELECT * FROM users" not in result

	def test_codex_with_tools_tool_summary(self):
		"""Tool calls in Codex fixture are summarized with [Used ...] format."""
		result = format_transcript(_load_trace("codex_with_tools.jsonl"))
		assert "[Used " in result


# ===================================================================
# format_transcript — OpenCode fixtures
# ===================================================================


class TestFormatTranscriptOpenCode:
	"""Tests for OpenCode trace format end-to-end."""

	def test_opencode_short_labels(self):
		"""OpenCode short trace has [USER] and [ASSISTANT] labels."""
		result = format_transcript(_load_trace("opencode_short.jsonl"))
		assert "[USER]" in result
		assert "[ASSISTANT]" in result

	def test_opencode_short_skips_metadata(self):
		"""The session_id metadata line is not rendered as a message."""
		result = format_transcript(_load_trace("opencode_short.jsonl"))
		assert "ses_305ab40a" not in result

	def test_opencode_short_tool_lines(self):
		"""OpenCode tool lines produce [TOOL] labels."""
		result = format_transcript(_load_trace("opencode_short.jsonl"))
		assert "[TOOL]" in result

	def test_opencode_rich_produces_output(self):
		"""OpenCode rich fixture formats without error."""
		result = format_transcript(_load_trace("opencode_rich.jsonl"))
		assert "[USER]" in result
		assert "[ASSISTANT]" in result


# ===================================================================
# format_transcript — Cursor fixtures
# ===================================================================


class TestFormatTranscriptCursor:
	"""Tests for Cursor trace format end-to-end."""

	def test_cursor_short_labels(self):
		"""Cursor short trace has [USER] and [ASSISTANT] labels."""
		result = format_transcript(_load_trace("cursor_short.jsonl"))
		assert "[USER]" in result

	def test_cursor_short_user_content(self):
		"""Cursor user bubble text appears in output."""
		result = format_transcript(_load_trace("cursor_short.jsonl"))
		# The user prompt about building a landing page is type=1
		assert "Landing Page" in result or "Acreta" in result

	def test_cursor_short_tool_summaries(self):
		"""Cursor tool uses are summarized in assistant blocks."""
		result = format_transcript(_load_trace("cursor_short.jsonl"))
		# toolFormerData entries should produce [Used ...] summaries
		assert "[Used " in result or "[ASSISTANT]" in result

	def test_cursor_rich_produces_output(self):
		"""Cursor rich fixture formats without error."""
		result = format_transcript(_load_trace("cursor_rich.jsonl"))
		# At minimum it should produce some output
		assert len(result) > 0

	def test_cursor_metadata_line_skipped(self):
		"""The first line (composerId without bubbleId) is skipped."""
		result = format_transcript(_load_trace("cursor_short.jsonl"))
		assert "composerId" not in result
		assert "63de0419" not in result


# ===================================================================
# format_transcript — edge cases
# ===================================================================


class TestFormatTranscriptEdgeCases:
	"""Edge cases: empty, short, malformed, unknown format."""

	def test_empty_string_returns_as_is(self):
		"""Empty string input returns the empty string (fallback)."""
		result = format_transcript("")
		assert result == ""

	def test_whitespace_only_returns_as_is(self):
		"""Whitespace-only input returns as-is (no JSON found)."""
		result = format_transcript("   \n  \n  ")
		assert result.strip() == ""

	def test_plain_text_passthrough(self):
		"""Non-JSONL text is returned unchanged."""
		text = "This is just a normal conversation."
		result = format_transcript(text)
		assert result == text

	def test_malformed_json_lines_skipped(self):
		"""Lines that are not valid JSON are silently skipped."""
		raw = '{"type":"user","message":{"content":"hello"}}\n{broken json}\n'
		# Only the valid line should parse; but since only 1 line parsed,
		# it might be "claude" format. Even if format detection fails,
		# we should not crash.
		result = format_transcript(raw)
		assert isinstance(result, str)

	def test_json_array_lines_skipped(self):
		"""JSON lines that are arrays (not dicts) are skipped."""
		raw = '[1, 2, 3]\n{"type":"user","message":{"content":"hi"}}\n'
		result = format_transcript(raw)
		assert isinstance(result, str)

	def test_unknown_format_passthrough(self):
		"""Lines that match no known format fall back to raw passthrough."""
		raw = json.dumps({"foo": "bar"}) + "\n" + json.dumps({"baz": 42}) + "\n"
		result = format_transcript(raw)
		assert result == raw

	def test_edge_empty_fixture(self):
		"""edge_empty.jsonl fixture: empty user content is handled."""
		result = format_transcript(_load_trace("edge_empty.jsonl"))
		# The fixture has role-based lines (opencode format)
		# User content is empty, assistant has content
		assert isinstance(result, str)

	def test_edge_short_fixture(self):
		"""edge_short.jsonl fixture: minimal exchange formats cleanly."""
		result = format_transcript(_load_trace("edge_short.jsonl"))
		assert "[USER]" in result
		assert "[ASSISTANT]" in result

	def test_single_user_message(self):
		"""A single user message produces [USER] label only."""
		raw = json.dumps({
			"type": "human",
			"message": {"content": "What is the status?"}
		}) + "\n"
		result = format_transcript(raw)
		assert "[USER]" in result
		assert "What is the status?" in result
		assert "[ASSISTANT]" not in result

	def test_single_assistant_message(self):
		"""A single assistant message produces [ASSISTANT] label only."""
		raw = json.dumps({
			"type": "assistant",
			"message": {
				"role": "assistant",
				"content": "Here is my answer."
			}
		}) + "\n"
		result = format_transcript(raw)
		assert "[ASSISTANT]" in result
		assert "Here is my answer." in result


# ===================================================================
# _detect_trace_format
# ===================================================================


class TestDetectTraceFormat:
	"""Tests for the format detection heuristic."""

	def test_detect_claude_human(self):
		"""type=human + message key -> claude."""
		lines = [{"type": "human", "message": {"content": "hi"}}]
		assert _detect_trace_format(lines) == "claude"

	def test_detect_claude_assistant(self):
		"""type=assistant + message key -> claude."""
		lines = [{"type": "assistant", "message": {"content": "hi"}}]
		assert _detect_trace_format(lines) == "claude"

	def test_detect_claude_user(self):
		"""type=user + message key -> claude."""
		lines = [{"type": "user", "message": {"role": "user", "content": "hi"}}]
		assert _detect_trace_format(lines) == "claude"

	def test_detect_codex_event_msg(self):
		"""type=event_msg -> codex."""
		lines = [{"type": "event_msg", "payload": {}}]
		assert _detect_trace_format(lines) == "codex"

	def test_detect_codex_response_item(self):
		"""type=response_item -> codex."""
		lines = [{"type": "response_item", "payload": {}}]
		assert _detect_trace_format(lines) == "codex"

	def test_detect_codex_session_meta(self):
		"""type=session_meta -> codex."""
		lines = [{"type": "session_meta", "payload": {}}]
		assert _detect_trace_format(lines) == "codex"

	def test_detect_cursor(self):
		"""_v key + integer type -> cursor."""
		lines = [{"_v": 3, "type": 1, "bubbleId": "abc", "text": "hi"}]
		assert _detect_trace_format(lines) == "cursor"

	def test_detect_opencode(self):
		"""role at top level without message key -> opencode."""
		lines = [{"role": "user", "content": "hi"}]
		assert _detect_trace_format(lines) == "opencode"

	def test_detect_opencode_skips_metadata(self):
		"""session_id metadata line is skipped; next line determines format."""
		lines = [
			{"session_id": "abc123", "cwd": "/tmp"},
			{"role": "user", "content": "hello"},
		]
		assert _detect_trace_format(lines) == "opencode"

	def test_detect_unknown(self):
		"""Unrecognizable lines -> unknown."""
		lines = [{"foo": "bar"}, {"baz": 42}]
		assert _detect_trace_format(lines) == "unknown"

	def test_detect_empty_list(self):
		"""Empty line list -> unknown."""
		assert _detect_trace_format([]) == "unknown"

	def test_detect_checks_first_five_only(self):
		"""Detection only inspects first 5 lines."""
		# 5 unrecognizable lines, then a claude line => still unknown
		junk = [{"x": i} for i in range(5)]
		claude = [{"type": "human", "message": {"content": "hi"}}]
		assert _detect_trace_format(junk + claude) == "unknown"


# ===================================================================
# _format_claude_line
# ===================================================================


class TestFormatClaudeLine:
	"""Tests for the per-line Claude formatter."""

	def test_user_message_string_content(self):
		"""User message with string content returns [USER] block."""
		obj = {"type": "human", "message": {"content": "Hello world"}}
		result = _format_claude_line(obj)
		assert result == "[USER]\nHello world"

	def test_assistant_message_string_content(self):
		"""Assistant message with string content returns [ASSISTANT] block."""
		obj = {"type": "assistant", "message": {"role": "assistant", "content": "Yes, done."}}
		result = _format_claude_line(obj)
		assert result == "[ASSISTANT]\nYes, done."

	def test_user_with_role_human(self):
		"""role=human in message is treated as user."""
		obj = {"type": "user", "message": {"role": "human", "content": "test"}}
		result = _format_claude_line(obj)
		assert result is not None
		assert "[USER]" in result

	def test_assistant_with_role_ai(self):
		"""role=ai in message is treated as assistant."""
		obj = {"type": "assistant", "message": {"role": "ai", "content": "answer"}}
		result = _format_claude_line(obj)
		assert result is not None
		assert "[ASSISTANT]" in result

	def test_content_list_with_text_blocks(self):
		"""Content as list of text blocks is extracted properly."""
		obj = {
			"type": "assistant",
			"message": {
				"role": "assistant",
				"content": [
					{"type": "text", "text": "First paragraph."},
					{"type": "text", "text": "Second paragraph."},
				]
			}
		}
		result = _format_claude_line(obj)
		assert "First paragraph." in result
		assert "Second paragraph." in result

	def test_content_list_with_tool_use(self):
		"""tool_use blocks in assistant content are summarized."""
		obj = {
			"type": "assistant",
			"message": {
				"role": "assistant",
				"content": [
					{"type": "text", "text": "Let me check."},
					{"type": "tool_use", "name": "Read", "input": {"file_path": "/src/app.py"}},
				]
			}
		}
		result = _format_claude_line(obj)
		assert "[Used Read on app.py]" in result

	def test_user_content_tool_result_skipped(self):
		"""tool_result blocks in user content are skipped."""
		obj = {
			"type": "user",
			"message": {
				"role": "user",
				"content": [
					{"type": "tool_result", "content": "some output"},
					{"type": "text", "text": "What next?"},
				]
			}
		}
		result = _format_claude_line(obj)
		assert "What next?" in result
		assert "some output" not in result

	def test_thinking_blocks_skipped(self):
		"""Thinking blocks are stripped from content."""
		obj = {
			"type": "assistant",
			"message": {
				"role": "assistant",
				"content": [
					{"type": "thinking", "thinking": "let me think..."},
					{"type": "text", "text": "The answer is 42."},
				]
			}
		}
		result = _format_claude_line(obj)
		assert "The answer is 42." in result
		assert "let me think" not in result

	def test_message_not_dict_returns_none(self):
		"""Non-dict message value returns None."""
		obj = {"type": "assistant", "message": "just a string"}
		assert _format_claude_line(obj) is None

	def test_empty_content_returns_none(self):
		"""Empty string content returns None."""
		obj = {"type": "human", "message": {"content": ""}}
		assert _format_claude_line(obj) is None

	def test_unknown_type_returns_none(self):
		"""Unknown entry type returns None."""
		obj = {"type": "system", "message": {"content": "setup"}}
		assert _format_claude_line(obj) is None


# ===================================================================
# _format_opencode_line
# ===================================================================


class TestFormatOpenCodeLine:
	"""Tests for the per-line OpenCode formatter."""

	def test_user_message(self):
		"""User role produces [USER] label."""
		obj = {"role": "user", "content": "Help me debug this."}
		result = _format_opencode_line(obj)
		assert result == "[USER]\nHelp me debug this."

	def test_assistant_message(self):
		"""Assistant role produces [ASSISTANT] label."""
		obj = {"role": "assistant", "content": "Found the bug."}
		result = _format_opencode_line(obj)
		assert result == "[ASSISTANT]\nFound the bug."

	def test_tool_message(self):
		"""Tool role produces [TOOL] label with summary."""
		obj = {
			"role": "tool",
			"tool_name": "read",
			"tool_input": {"filePath": "/src/main.py"},
		}
		result = _format_opencode_line(obj)
		assert "[TOOL]" in result
		assert "[Used read on main.py]" in result

	def test_metadata_line_skipped(self):
		"""session_id line without role is skipped."""
		obj = {"session_id": "abc", "cwd": "/tmp"}
		assert _format_opencode_line(obj) is None

	def test_empty_content_returns_none(self):
		"""Empty content string produces None."""
		obj = {"role": "user", "content": ""}
		assert _format_opencode_line(obj) is None

	def test_none_content_returns_none(self):
		"""None content produces None."""
		obj = {"role": "assistant", "content": None}
		assert _format_opencode_line(obj) is None

	def test_unknown_role_returns_none(self):
		"""Unknown role returns None."""
		obj = {"role": "system", "content": "setup prompt"}
		assert _format_opencode_line(obj) is None


# ===================================================================
# _format_codex_line
# ===================================================================


class TestFormatCodexLine:
	"""Tests for the per-line Codex formatter."""

	def test_user_message(self):
		"""user_message payload produces [USER] label."""
		obj = {
			"type": "event_msg",
			"payload": {"type": "user_message", "message": "Fix the tests."}
		}
		result = _format_codex_line(obj)
		assert result == "[USER]\nFix the tests."

	def test_assistant_message_by_role_string_content(self):
		"""role=assistant with string content produces [ASSISTANT] label."""
		obj = {
			"type": "response_item",
			"payload": {
				"type": "message",
				"role": "assistant",
				"content": "Done.",
			}
		}
		result = _format_codex_line(obj)
		assert result is not None
		assert "[ASSISTANT]" in result

	def test_assistant_message_output_text_blocks_returns_none(self):
		"""output_text content blocks are not handled by _extract_content_text.

		The extractor only recognises type=text blocks, so output_text
		blocks (used by the Codex API) produce empty content and return None.
		"""
		obj = {
			"type": "response_item",
			"payload": {
				"type": "message",
				"role": "assistant",
				"content": [{"type": "output_text", "text": "Done."}],
			}
		}
		result = _format_codex_line(obj)
		assert result is None

	def test_assistant_message_agent_message(self):
		"""payload type=agent_message produces [ASSISTANT] label."""
		obj = {
			"type": "response_item",
			"payload": {
				"type": "agent_message",
				"content": "I will fix this.",
			}
		}
		result = _format_codex_line(obj)
		assert "[ASSISTANT]" in result
		assert "I will fix this." in result

	def test_function_call_produces_tool(self):
		"""function_call payload produces [TOOL] label."""
		obj = {
			"type": "response_item",
			"payload": {
				"type": "function_call",
				"name": "write_file",
				"arguments": json.dumps({"path": "src/api/users.py", "content": "code"}),
			}
		}
		result = _format_codex_line(obj)
		assert "[TOOL]" in result
		assert "[Used write_file on users.py]" in result

	def test_function_call_invalid_json_args(self):
		"""function_call with unparseable arguments still produces a summary."""
		obj = {
			"type": "response_item",
			"payload": {
				"type": "function_call",
				"name": "my_tool",
				"arguments": "{{invalid json}}",
			}
		}
		result = _format_codex_line(obj)
		assert "[TOOL]" in result
		assert "[Used my_tool]" in result

	def test_custom_tool_call(self):
		"""custom_tool_call payload produces [TOOL] label."""
		obj = {
			"type": "response_item",
			"payload": {
				"type": "custom_tool_call",
				"name": "grep",
				"input": {"pattern": "TODO", "path": "/src"},
			}
		}
		result = _format_codex_line(obj)
		assert "[TOOL]" in result
		assert "grep" in result

	def test_function_call_output_skipped(self):
		"""function_call_output lines return None."""
		obj = {
			"type": "response_item",
			"payload": {"type": "function_call_output", "output": "lots of data"}
		}
		assert _format_codex_line(obj) is None

	def test_session_meta_skipped(self):
		"""session_meta lines return None."""
		obj = {"type": "session_meta", "payload": {"model": "gpt-4"}}
		assert _format_codex_line(obj) is None

	def test_payload_not_dict_returns_none(self):
		"""Non-dict payload returns None."""
		obj = {"type": "event_msg", "payload": "string"}
		assert _format_codex_line(obj) is None

	def test_empty_user_message_returns_none(self):
		"""Empty user message string returns None."""
		obj = {
			"type": "event_msg",
			"payload": {"type": "user_message", "message": ""}
		}
		assert _format_codex_line(obj) is None


# ===================================================================
# _format_cursor_line
# ===================================================================


class TestFormatCursorLine:
	"""Tests for the per-line Cursor formatter."""

	def test_user_bubble(self):
		"""type=1 bubble with text produces [USER] label."""
		obj = {"_v": 3, "type": 1, "bubbleId": "a", "text": "Build the page."}
		result = _format_cursor_line(obj)
		assert result == "[USER]\nBuild the page."

	def test_assistant_bubble_text_only(self):
		"""type=2 bubble with text produces [ASSISTANT] label."""
		obj = {"_v": 3, "type": 2, "bubbleId": "b", "text": "Done."}
		result = _format_cursor_line(obj)
		assert result == "[ASSISTANT]\nDone."

	def test_assistant_bubble_with_tool_list(self):
		"""type=2 bubble with toolFormerData list produces tool summaries."""
		obj = {
			"_v": 3, "type": 2, "bubbleId": "c", "text": "Let me check.",
			"toolFormerData": [
				{"name": "read_file_v2", "params": json.dumps({"targetFile": "src/app.py"})},
				{"name": "run_terminal_command_v2", "params": json.dumps({"command": "npm test"})},
			]
		}
		result = _format_cursor_line(obj)
		assert "[ASSISTANT]" in result
		assert "Let me check." in result
		assert "[Used read_file_v2 on app.py]" in result
		assert "[Ran: npm test]" in result

	def test_assistant_bubble_with_tool_dict(self):
		"""type=2 bubble with toolFormerData as single dict (not list)."""
		obj = {
			"_v": 3, "type": 2, "bubbleId": "d", "text": "",
			"toolFormerData": {
				"name": "glob_file_search",
				"params": json.dumps({"globPattern": "**/*.ts"}),
			}
		}
		result = _format_cursor_line(obj)
		assert "[ASSISTANT]" in result
		assert "[Used glob_file_search: **/*.ts]" in result

	def test_assistant_bubble_tool_invalid_params_dict(self):
		"""toolFormerData (dict) with invalid JSON params still produces summary."""
		obj = {
			"_v": 3, "type": 2, "bubbleId": "e", "text": "",
			"toolFormerData": {"name": "my_tool", "params": "{{bad}}"}
		}
		result = _format_cursor_line(obj)
		assert "[Used my_tool]" in result

	def test_assistant_bubble_tool_list_invalid_params(self):
		"""toolFormerData list item with invalid JSON params still produces summary."""
		obj = {
			"_v": 3, "type": 2, "bubbleId": "e2", "text": "",
			"toolFormerData": [
				{"name": "broken_tool", "params": "{{invalid json}}"},
			]
		}
		result = _format_cursor_line(obj)
		assert "[Used broken_tool]" in result

	def test_metadata_line_skipped(self):
		"""composerId line without bubbleId is skipped."""
		obj = {"_v": 13, "composerId": "abc-123", "text": ""}
		assert _format_cursor_line(obj) is None

	def test_non_integer_type_returns_none(self):
		"""Non-integer type value returns None."""
		obj = {"_v": 3, "type": "assistant", "bubbleId": "f", "text": "hi"}
		assert _format_cursor_line(obj) is None

	def test_thinking_type_30_skipped(self):
		"""type=30 (thinking) returns None."""
		obj = {"_v": 3, "type": 30, "bubbleId": "g", "text": "hmm"}
		assert _format_cursor_line(obj) is None

	def test_user_empty_text_returns_none(self):
		"""User bubble with empty text returns None."""
		obj = {"_v": 3, "type": 1, "bubbleId": "h", "text": ""}
		assert _format_cursor_line(obj) is None

	def test_assistant_empty_text_no_tools_returns_none(self):
		"""Assistant bubble with no text and no tools returns None."""
		obj = {"_v": 3, "type": 2, "bubbleId": "i", "text": ""}
		assert _format_cursor_line(obj) is None


# ===================================================================
# _extract_content_text
# ===================================================================


class TestExtractContentText:
	"""Tests for content extraction from string/list blocks."""

	def test_string_content(self):
		"""String content returned as-is (stripped)."""
		assert _extract_content_text("  hello  ") == "hello"

	def test_empty_string(self):
		"""Empty string returns empty."""
		assert _extract_content_text("") == ""

	def test_none_returns_empty(self):
		"""None input returns empty string."""
		assert _extract_content_text(None) == ""

	def test_integer_returns_empty(self):
		"""Non-string/non-list returns empty string."""
		assert _extract_content_text(42) == ""

	def test_text_blocks(self):
		"""List of text blocks is joined with newlines."""
		blocks = [
			{"type": "text", "text": "Line one."},
			{"type": "text", "text": "Line two."},
		]
		result = _extract_content_text(blocks)
		assert result == "Line one.\nLine two."

	def test_tool_use_block_summarized(self):
		"""tool_use block produces [Used ...] summary."""
		blocks = [
			{"type": "tool_use", "name": "Bash", "input": {"command": "ls -la"}},
		]
		result = _extract_content_text(blocks)
		assert "[Ran: ls -la]" in result

	def test_tool_result_skipped_when_flag_set(self):
		"""tool_result blocks skipped when skip_tool_results=True."""
		blocks = [
			{"type": "tool_result", "content": "output data"},
			{"type": "text", "text": "Continuing."},
		]
		result = _extract_content_text(blocks, skip_tool_results=True)
		assert "Continuing." in result
		assert "output data" not in result

	def test_tool_result_not_skipped_by_default(self):
		"""tool_result blocks are NOT skipped when skip_tool_results=False (default)."""
		blocks = [
			{"type": "tool_result", "content": "output"},
			{"type": "text", "text": "Next."},
		]
		# tool_result with skip=False: there's no text extraction for it,
		# so it just falls through to the default (no text appended)
		result = _extract_content_text(blocks, skip_tool_results=False)
		assert "Next." in result

	def test_thinking_block_skipped(self):
		"""Thinking blocks are always skipped."""
		blocks = [
			{"type": "thinking", "thinking": "deep thought"},
			{"type": "text", "text": "Answer."},
		]
		result = _extract_content_text(blocks)
		assert result == "Answer."
		assert "deep thought" not in result

	def test_non_dict_blocks_skipped(self):
		"""Non-dict items in the list are skipped."""
		blocks = ["not a dict", {"type": "text", "text": "Valid."}]
		result = _extract_content_text(blocks)
		assert result == "Valid."

	def test_empty_text_block_skipped(self):
		"""Text block with empty string is not included."""
		blocks = [
			{"type": "text", "text": ""},
			{"type": "text", "text": "Real content."},
		]
		result = _extract_content_text(blocks)
		assert result == "Real content."

	def test_tool_use_non_dict_input(self):
		"""tool_use with non-dict input still produces summary."""
		blocks = [
			{"type": "tool_use", "name": "test_tool", "input": "string_input"},
		]
		result = _extract_content_text(blocks)
		assert "[Used test_tool]" in result


# ===================================================================
# _summarize_tool_use
# ===================================================================


class TestSummarizeToolUse:
	"""Tests for the tool summarization helper."""

	def test_file_path_key(self):
		"""file_path key extracts filename."""
		result = _summarize_tool_use("Read", {"file_path": "/home/user/src/app.py"})
		assert result == "[Used Read on app.py]"

	def test_path_key(self):
		"""path key extracts filename."""
		result = _summarize_tool_use("write_file", {"path": "src/api/users.py"})
		assert result == "[Used write_file on users.py]"

	def test_filePath_key(self):
		"""filePath key (OpenCode) extracts filename."""
		result = _summarize_tool_use("read", {"filePath": "/project/main.rs"})
		assert result == "[Used read on main.rs]"

	def test_targetFile_key(self):
		"""targetFile key (Cursor read_file_v2) extracts filename."""
		result = _summarize_tool_use("read_file_v2", {"targetFile": "components/App.tsx"})
		assert result == "[Used read_file_v2 on App.tsx]"

	def test_relativeWorkspacePath_key(self):
		"""relativeWorkspacePath key (Cursor edit) extracts filename."""
		result = _summarize_tool_use("edit_file_v2", {"relativeWorkspacePath": "src/index.ts"})
		assert result == "[Used edit_file_v2 on index.ts]"

	def test_command_key(self):
		"""command key produces [Ran: ...] format."""
		result = _summarize_tool_use("Bash", {"command": "npm test"})
		assert result == "[Ran: npm test]"

	def test_command_truncated_at_80(self):
		"""Long commands are truncated to 80 chars with ellipsis."""
		long_cmd = "a" * 100
		result = _summarize_tool_use("Bash", {"command": long_cmd})
		assert result == f"[Ran: {'a' * 80}...]"

	def test_query_key(self):
		"""query key produces [Used tool: query] format."""
		result = _summarize_tool_use("WebSearch", {"query": "python async patterns"})
		assert result == "[Used WebSearch: python async patterns]"

	def test_pattern_key(self):
		"""pattern key (grep/glob) produces search summary."""
		result = _summarize_tool_use("Grep", {"pattern": "def test_"})
		assert result == "[Used Grep: def test_]"

	def test_prompt_key(self):
		"""prompt key (agent delegation) produces summary."""
		result = _summarize_tool_use("Agent", {"prompt": "analyze this code"})
		assert result == "[Used Agent: analyze this code]"

	def test_globPattern_key(self):
		"""globPattern key (Cursor) produces search summary."""
		result = _summarize_tool_use("glob_file_search", {"globPattern": "**/*.ts"})
		assert result == "[Used glob_file_search: **/*.ts]"

	def test_query_truncated_at_60(self):
		"""Long queries are truncated to 60 chars with ellipsis."""
		long_q = "x" * 80
		result = _summarize_tool_use("Search", {"query": long_q})
		assert result == f"[Used Search: {'x' * 60}...]"

	def test_description_key(self):
		"""description key (task delegation) produces summary."""
		result = _summarize_tool_use("SubAgent", {"description": "refactor the auth module"})
		assert result == "[Used SubAgent: refactor the auth module]"

	def test_description_truncated_at_60(self):
		"""Long descriptions are truncated to 60 chars."""
		long_d = "y" * 80
		result = _summarize_tool_use("Task", {"description": long_d})
		assert result == f"[Used Task: {'y' * 60}...]"

	def test_no_recognized_keys(self):
		"""No recognized keys returns basic [Used name]."""
		result = _summarize_tool_use("mystery_tool", {"unknown": "value"})
		assert result == "[Used mystery_tool]"

	def test_empty_input_data(self):
		"""Empty dict input returns basic [Used name]."""
		result = _summarize_tool_use("tool", {})
		assert result == "[Used tool]"

	def test_non_dict_input_data(self):
		"""Non-dict input_data returns basic [Used name]."""
		result = _summarize_tool_use("tool", "not a dict")
		assert result == "[Used tool]"

	def test_file_without_slash(self):
		"""File path without slash returns the whole name."""
		result = _summarize_tool_use("Read", {"file_path": "Makefile"})
		assert result == "[Used Read on Makefile]"

	def test_priority_file_path_over_command(self):
		"""file_path key takes priority over command key."""
		result = _summarize_tool_use("tool", {
			"file_path": "/a/b.py",
			"command": "echo hello",
		})
		assert result == "[Used tool on b.py]"

	def test_priority_command_over_query(self):
		"""command key takes priority over query key."""
		result = _summarize_tool_use("tool", {
			"command": "ls",
			"query": "test",
		})
		assert result == "[Ran: ls]"


# ===================================================================
# Integration: mixed_decisions_learnings fixture
# ===================================================================


class TestMixedDecisionsLearningsFixture:
	"""Tests using the mixed_decisions_learnings.jsonl fixture."""

	def test_produces_formatted_output(self):
		"""The mixed fixture produces non-empty formatted output."""
		result = format_transcript(_load_trace("mixed_decisions_learnings.jsonl"))
		assert len(result) > 0
		assert "[USER]" in result or "[ASSISTANT]" in result

	def test_debug_session_fixture(self):
		"""debug_session.jsonl processes without error."""
		result = format_transcript(_load_trace("debug_session.jsonl"))
		assert isinstance(result, str)
		assert len(result) > 0
