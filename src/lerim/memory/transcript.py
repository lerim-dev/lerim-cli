"""Transcript formatting utilities for session trace files.

Converts raw JSONL session traces from various coding agents (Claude, OpenCode,
Codex, Cursor) into clean [USER]/[ASSISTANT] formatted text suitable for
LLM analysis.
"""

from __future__ import annotations

import json


def format_transcript(raw: str) -> str:
	"""Convert compacted JSONL transcript to clean conversation format for extraction.

	Supports 4 agent formats: Claude, OpenCode, Codex, Cursor.
	Strips metadata noise, clears tool inputs, adds [USER]/[ASSISTANT] speaker labels.
	Returns plain text conversation — not JSONL.
	"""
	lines_parsed: list[dict] = []
	for line in raw.split("\n"):
		line = line.strip()
		if not line:
			continue
		try:
			obj = json.loads(line)
			if isinstance(obj, dict):
				lines_parsed.append(obj)
		except (json.JSONDecodeError, ValueError):
			continue

	if not lines_parsed:
		return raw  # fallback: return as-is if no JSON found

	fmt = _detect_trace_format(lines_parsed)
	formatter = {
		"claude": _format_claude_line,
		"opencode": _format_opencode_line,
		"codex": _format_codex_line,
		"cursor": _format_cursor_line,
	}.get(fmt)

	if not formatter:
		return raw  # unknown format, pass through

	parts: list[str] = []
	for obj in lines_parsed:
		result = formatter(obj)
		if result:
			parts.append(result)

	formatted = "\n\n".join(parts)
	return formatted + "\n" if formatted else raw


def _detect_trace_format(lines: list[dict]) -> str:
	"""Detect which agent produced this trace by inspecting line structure."""
	for obj in lines[:5]:  # check first 5 lines
		# Claude: has "type" in ("user","assistant","human") and "message" key
		if obj.get("type") in ("user", "assistant", "human") and "message" in obj:
			return "claude"
		# Codex: has "type" in ("event_msg","response_item","session_meta")
		if obj.get("type") in ("event_msg", "response_item", "session_meta"):
			return "codex"
		# Cursor: has "_v" key and integer "type"
		if "_v" in obj and isinstance(obj.get("type"), int):
			return "cursor"
		# OpenCode: has "role" at top level (not nested in message)
		if "role" in obj and "message" not in obj:
			return "opencode"
		# OpenCode metadata line (first line)
		if "session_id" in obj:
			continue  # skip metadata, check next line
	return "unknown"


def _format_claude_line(obj: dict) -> str | None:
	"""Format one Claude compacted JSONL line."""
	entry_type = obj.get("type", "")
	msg = obj.get("message", {})
	if not isinstance(msg, dict):
		return None

	role = msg.get("role", entry_type)
	content = msg.get("content")

	if role in ("user", "human"):
		text = _extract_content_text(content, skip_tool_results=True)
		if text:
			return f"[USER]\n{text}"
	elif role in ("assistant", "ai"):
		# "ai" is used by some LangChain-style traces
		text = _extract_content_text(content, skip_tool_results=False)
		if text:
			return f"[ASSISTANT]\n{text}"

	return None


def _format_opencode_line(obj: dict) -> str | None:
	"""Format one OpenCode compacted JSONL line."""
	# Skip metadata line
	if "session_id" in obj and "role" not in obj:
		return None

	role = obj.get("role", "")
	content = obj.get("content", "")

	if role == "user":
		text = str(content).strip() if content else ""
		if text:
			return f"[USER]\n{text}"
	elif role == "assistant":
		text = str(content).strip() if content else ""
		if text:
			return f"[ASSISTANT]\n{text}"
	elif role == "tool":
		tool_name = obj.get("tool_name", "tool")
		tool_input = obj.get("tool_input", {})
		summary = _summarize_tool_use(tool_name, tool_input)
		return f"[TOOL]\n{summary}"

	return None


def _format_codex_line(obj: dict) -> str | None:
	"""Format one Codex compacted JSONL line."""
	entry_type = obj.get("type", "")
	payload = obj.get("payload", {})
	if not isinstance(payload, dict):
		return None

	# Skip metadata
	if entry_type == "session_meta":
		return None

	payload_type = payload.get("type", "")

	# User message
	if payload_type == "user_message" or (entry_type == "event_msg" and payload_type == "user_message"):
		text = str(payload.get("message", "")).strip()
		if text:
			return f"[USER]\n{text}"

	# Assistant message content
	role = payload.get("role", "")
	if role == "assistant" or payload_type in ("agent_message", "message"):
		content = payload.get("content")
		text = _extract_content_text(content, skip_tool_results=False)
		if text:
			return f"[ASSISTANT]\n{text}"

	# Function call
	if payload_type == "function_call":
		name = payload.get("name", "tool")
		args_raw = payload.get("arguments", "{}")
		try:
			args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
		except (json.JSONDecodeError, ValueError):
			args = {}
		summary = _summarize_tool_use(name, args if isinstance(args, dict) else {})
		return f"[TOOL]\n{summary}"

	# Custom tool call
	if payload_type == "custom_tool_call":
		name = payload.get("name", "tool")
		input_data = payload.get("input", {})
		summary = _summarize_tool_use(name, input_data if isinstance(input_data, dict) else {})
		return f"[TOOL]\n{summary}"

	# Skip function_call_output (already cleared)
	if payload_type == "function_call_output":
		return None

	return None


def _format_cursor_line(obj: dict) -> str | None:
	"""Format one Cursor compacted JSONL line."""
	# Skip metadata line (has composerId but no bubbleId)
	if "composerId" in obj and "bubbleId" not in obj:
		return None

	bubble_type = obj.get("type")
	if not isinstance(bubble_type, int):
		return None

	text = str(obj.get("text", "")).strip()

	# Type 1 = user
	if bubble_type == 1:
		if text:
			return f"[USER]\n{text}"
		return None

	# Type 2 = assistant
	if bubble_type == 2:
		parts = []
		if text:
			parts.append(text)

		# Tool uses from toolFormerData
		tool_data = obj.get("toolFormerData")
		if isinstance(tool_data, list):
			for td in tool_data:
				if isinstance(td, dict):
					name = td.get("name", "tool")
					params_raw = td.get("params", "{}")
					try:
						params = json.loads(params_raw) if isinstance(params_raw, str) else params_raw
					except (json.JSONDecodeError, ValueError):
						params = {}
					summary = _summarize_tool_use(name, params if isinstance(params, dict) else {})
					parts.append(summary)
		elif isinstance(tool_data, dict):
			name = tool_data.get("name", "tool")
			params_raw = tool_data.get("params", "{}")
			try:
				params = json.loads(params_raw) if isinstance(params_raw, str) else params_raw
			except (json.JSONDecodeError, ValueError):
				params = {}
			summary = _summarize_tool_use(name, params if isinstance(params, dict) else {})
			parts.append(summary)

		if parts:
			return "[ASSISTANT]\n" + "\n".join(parts)
		return None

	# Type 30 = thinking, skip
	return None


def _extract_content_text(content, *, skip_tool_results: bool = False) -> str:
	"""Extract readable text from content (string or list of blocks).

	Handles Claude/Codex content format: string or list of
	{type: "text", text: "..."} / {type: "tool_use", ...} / {type: "tool_result", ...} blocks.
	"""
	if isinstance(content, str):
		return content.strip()

	if not isinstance(content, list):
		return ""

	texts: list[str] = []
	for block in content:
		if not isinstance(block, dict):
			continue
		btype = block.get("type", "")

		if btype == "text":
			t = str(block.get("text", "")).strip()
			if t:
				texts.append(t)

		elif btype == "tool_use":
			name = block.get("name", "tool")
			input_data = block.get("input", {})
			summary = _summarize_tool_use(name, input_data if isinstance(input_data, dict) else {})
			texts.append(summary)

		elif btype == "tool_result" and skip_tool_results:
			continue  # skip cleared tool results

		elif btype == "thinking":
			continue  # skip thinking blocks entirely

	return "\n".join(texts)


def _summarize_tool_use(name: str, input_data: dict) -> str:
	"""One-line summary of a tool use without full content."""
	if not isinstance(input_data, dict):
		return f"[Used {name}]"

	# File tools: show just the filename
	# Covers: file_path (Claude/Codex), path (generic), filePath (OpenCode),
	# targetFile (Cursor read_file_v2), relativeWorkspacePath (Cursor edit_file_v2)
	for key in ("file_path", "path", "filePath", "targetFile", "relativeWorkspacePath"):
		path = input_data.get(key, "")
		if path:
			short = str(path).rsplit("/", 1)[-1] if "/" in str(path) else str(path)
			return f"[Used {name} on {short}]"

	# Bash/shell: show truncated command
	cmd = input_data.get("command", "")
	if cmd:
		short_cmd = str(cmd)[:80] + ("..." if len(str(cmd)) > 80 else "")
		return f"[Ran: {short_cmd}]"

	# Search tools: show query
	# Covers: query, pattern (grep/glob), prompt (agent delegation), globPattern (Cursor)
	for key in ("query", "pattern", "prompt", "globPattern"):
		q = input_data.get(key, "")
		if q:
			short_q = str(q)[:60] + ("..." if len(str(q)) > 60 else "")
			return f"[Used {name}: {short_q}]"

	# Task/agent delegation: show description
	desc = input_data.get("description", "")
	if desc:
		short_d = str(desc)[:60] + ("..." if len(str(desc)) > 60 else "")
		return f"[Used {name}: {short_d}]"

	return f"[Used {name}]"
