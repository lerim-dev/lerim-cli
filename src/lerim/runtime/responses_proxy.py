"""Lightweight Responses API → Chat Completions proxy for Codex CLI.

The Codex CLI requires the Responses API wire format (/responses endpoint).
Many LLM providers (MiniMax, ZAI, Ollama, etc.) only support Chat Completions.
This module bridges the gap by running a tiny local HTTP server that:
  1. Accepts POST /responses (Responses API format)
  2. Converts to POST /v1/chat/completions
  3. Forwards to the real LLM provider
  4. Converts the response back to Responses API format

Used by codex_tool() when the configured provider doesn't support Responses API.
Based on https://github.com/n0sr3v/codex-responses-shim (MIT, 432 lines, zero deps).

Usage:
	proxy = ResponsesProxy(backend_url="https://api.minimax.io/v1", api_key="sk-...")
	proxy.start()  # starts on localhost:0 (random port)
	print(proxy.url)  # http://127.0.0.1:54321
	# ... use proxy.url as base_url for CodexOptions ...
	proxy.stop()
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


# ---------------------------------------------------------------------------
# Conversion functions (Responses API ↔ Chat Completions)
# ---------------------------------------------------------------------------

_CHAT_HISTORY: dict[str, list[dict]] = {}


def _now_ts() -> int:
	return int(time.time())


def _gen_id(prefix: str) -> str:
	return f"{prefix}_{uuid.uuid4().hex}"


def _extract_text(content) -> str:
	if content is None:
		return ""
	if isinstance(content, str):
		return content
	if isinstance(content, list):
		parts = []
		for item in content:
			if isinstance(item, str):
				parts.append(item)
			elif isinstance(item, dict):
				if item.get("type") in {"input_text", "output_text", "text"}:
					parts.append(item.get("text", ""))
		return "".join(parts)
	return str(content)


def _normalize_role(role: str) -> str:
	if role in {"developer", "system"}:
		return "system"
	if role in {"user", "assistant", "tool"}:
		return role
	return "user"


def _normalize_tools(request_tools) -> list[dict]:
	tools = []
	for tool in request_tools or []:
		if not isinstance(tool, dict) or tool.get("type") != "function":
			continue
		function = tool.get("function") if isinstance(tool.get("function"), dict) else tool
		name = function.get("name")
		if not name:
			continue
		tools.append({
			"type": "function",
			"function": {
				"name": name,
				"description": function.get("description", ""),
				"parameters": function.get("parameters", {"type": "object", "properties": {}}),
			},
		})
	return tools


def convert_request_to_chat(payload: dict) -> tuple[dict, list[dict]]:
	"""Convert a Responses API request payload to Chat Completions format.

	Returns (chat_payload, prior_messages) where prior_messages is the
	conversation history for subsequent response building.
	"""
	previous_response_id = payload.get("previous_response_id")
	messages = list(_CHAT_HISTORY.get(previous_response_id, []))

	instructions = payload.get("instructions")
	if instructions:
		if messages and messages[0].get("role") == "system":
			messages[0]["content"] = instructions
		else:
			messages.insert(0, {"role": "system", "content": instructions})

	pending_tool_calls: list[dict] = []

	for item in payload.get("input") or []:
		if isinstance(item, str):
			messages.append({"role": "user", "content": item})
			continue
		if not isinstance(item, dict):
			continue

		item_type = item.get("type")
		if item_type == "message":
			role = _normalize_role(item.get("role", "user"))
			messages.append({"role": role, "content": _extract_text(item.get("content"))})
		elif item_type == "function_call":
			pending_tool_calls.append({
				"id": item.get("call_id") or item.get("id") or _gen_id("call"),
				"type": "function",
				"function": {
					"name": item.get("name", ""),
					"arguments": item.get("arguments", ""),
				},
			})
		elif item_type == "function_call_output":
			if pending_tool_calls:
				messages.append({"role": "assistant", "content": None, "tool_calls": pending_tool_calls})
				pending_tool_calls = []
			messages.append({
				"role": "tool",
				"tool_call_id": item.get("call_id") or item.get("id") or "",
				"content": item.get("output", ""),
			})

	if pending_tool_calls:
		messages.append({"role": "assistant", "content": None, "tool_calls": pending_tool_calls})

	if not messages:
		messages = [{"role": "user", "content": ""}]

	# Merge system messages to top
	system_msgs = [m for m in messages if m.get("role") == "system"]
	other_msgs = [m for m in messages if m.get("role") != "system"]
	if system_msgs:
		merged_system = "\n\n".join(m.get("content") or "" for m in system_msgs if m.get("content"))
		messages = ([{"role": "system", "content": merged_system}] if merged_system else []) + other_msgs

	chat_payload: dict = {
		"model": payload.get("model"),
		"messages": messages,
		"stream": False,
	}

	if "temperature" in payload:
		chat_payload["temperature"] = payload["temperature"]
	if "top_p" in payload:
		chat_payload["top_p"] = payload["top_p"]
	if payload.get("max_output_tokens") is not None:
		chat_payload["max_tokens"] = payload["max_output_tokens"]

	tools = _normalize_tools(payload.get("tools"))
	if tools:
		chat_payload["tools"] = tools

	return chat_payload, messages


def build_response_from_chat(
	request_payload: dict, upstream: dict, prior_messages: list[dict]
) -> tuple[dict, list[dict]]:
	"""Convert a Chat Completions response to Responses API format.

	Returns (response, output_events) where output_events is a list of SSE events.
	"""
	response_id = _gen_id("resp")
	created_at = _now_ts()
	output: list[dict] = []
	output_events: list[dict] = []

	choice = (upstream.get("choices") or [{}])[0]
	message = choice.get("message") or {}
	tool_calls = message.get("tool_calls") or []
	text = message.get("content") or ""
	if isinstance(text, list):
		text = _extract_text(text)

	new_history = list(prior_messages)
	output_index = 0

	# Handle tool calls
	if tool_calls:
		assistant_tool_calls = []
		for tool_call in tool_calls:
			call_id = tool_call.get("id") or _gen_id("call")
			function = tool_call.get("function") or {}
			name = function.get("name", "")
			arguments = function.get("arguments", "")
			item = {
				"id": call_id,
				"type": "function_call",
				"status": "completed",
				"call_id": call_id,
				"name": name,
				"arguments": arguments,
			}
			output.append(item)
			assistant_tool_calls.append({
				"id": call_id,
				"type": "function",
				"function": {"name": name, "arguments": arguments},
			})
			output_events.extend([
				{"type": "response.output_item.added", "response_id": response_id, "output_index": output_index, "item": item},
				{"type": "response.function_call_arguments.delta", "response_id": response_id, "item_id": call_id, "output_index": output_index, "delta": arguments},
				{"type": "response.function_call_arguments.done", "response_id": response_id, "item_id": call_id, "output_index": output_index, "arguments": arguments},
				{"type": "response.output_item.done", "response_id": response_id, "output_index": output_index, "item": item},
			])
			output_index += 1
		new_history.append({"role": "assistant", "content": None, "tool_calls": assistant_tool_calls})

	# Handle text output
	if text:
		message_id = _gen_id("msg")
		part = {"type": "output_text", "text": text, "annotations": []}
		item = {
			"id": message_id,
			"type": "message",
			"status": "completed",
			"role": "assistant",
			"content": [part],
		}
		output.append(item)
		output_events.extend([
			{"type": "response.output_item.added", "response_id": response_id, "output_index": output_index, "item": {"id": message_id, "type": "message", "status": "in_progress", "role": "assistant", "content": []}},
			{"type": "response.content_part.added", "response_id": response_id, "item_id": message_id, "output_index": output_index, "content_index": 0, "part": {"type": "output_text", "text": "", "annotations": []}},
			{"type": "response.output_text.delta", "response_id": response_id, "item_id": message_id, "output_index": output_index, "content_index": 0, "delta": text},
			{"type": "response.output_text.done", "response_id": response_id, "item_id": message_id, "output_index": output_index, "content_index": 0, "text": text, "logprobs": []},
			{"type": "response.content_part.done", "response_id": response_id, "item_id": message_id, "output_index": output_index, "content_index": 0, "part": part},
			{"type": "response.output_item.done", "response_id": response_id, "output_index": output_index, "item": item},
		])
		output_index += 1
		new_history.append({"role": "assistant", "content": text})

	usage = upstream.get("usage") or {}
	response = {
		"id": response_id,
		"object": "response",
		"created_at": created_at,
		"status": "completed",
		"error": None,
		"incomplete_details": None,
		"instructions": request_payload.get("instructions"),
		"max_output_tokens": request_payload.get("max_output_tokens"),
		"model": upstream.get("model") or request_payload.get("model", ""),
		"output": output,
		"parallel_tool_calls": True,
		"previous_response_id": request_payload.get("previous_response_id"),
		"reasoning": {"effort": None, "summary": None},
		"store": request_payload.get("store", False),
		"temperature": request_payload.get("temperature", 1.0),
		"text": {"format": {"type": "text"}},
		"tool_choice": "auto",
		"tools": request_payload.get("tools") or [],
		"top_p": request_payload.get("top_p", 1.0),
		"truncation": "disabled",
		"usage": {
			"input_tokens": usage.get("prompt_tokens", 0),
			"output_tokens": usage.get("completion_tokens", 0),
			"total_tokens": usage.get("total_tokens", 0),
			"input_tokens_details": {"cached_tokens": 0},
			"output_tokens_details": {"reasoning_tokens": 0},
		},
		"user": request_payload.get("user"),
		"metadata": request_payload.get("metadata") or {},
	}

	_CHAT_HISTORY[response_id] = new_history
	return response, output_events


# ---------------------------------------------------------------------------
# HTTP Server (runs in a background thread)
# ---------------------------------------------------------------------------

class _ProxyHandler(BaseHTTPRequestHandler):
	"""HTTP handler that translates Responses API → Chat Completions."""

	protocol_version = "HTTP/1.1"
	backend_url: str = ""
	api_key: str = ""

	def log_message(self, fmt, *args):
		return  # silent

	def _send_json(self, status: int, payload: dict) -> None:
		data = json.dumps(payload).encode("utf-8")
		self.send_response(status)
		self.send_header("Content-Type", "application/json")
		self.send_header("Content-Length", str(len(data)))
		self.end_headers()
		self.wfile.write(data)

	def _send_sse(self, payload: dict) -> None:
		self.wfile.write(f"data: {json.dumps(payload)}\n\n".encode("utf-8"))
		self.wfile.flush()

	def do_GET(self):
		if self.path == "/health":
			return self._send_json(200, {"status": "ok"})
		self._send_json(404, {"error": "not_found"})

	def do_POST(self):
		if self.path != "/responses":
			return self._send_json(404, {"error": "not_found"})

		try:
			length = int(self.headers.get("Content-Length", "0"))
			payload = json.loads(self.rfile.read(length).decode("utf-8"))
			chat_payload, prior_messages = convert_request_to_chat(payload)

			headers = {"Content-Type": "application/json"}
			if self.api_key:
				headers["Authorization"] = f"Bearer {self.api_key}"

			upstream_req = Request(
				f"{self.backend_url}/chat/completions",
				data=json.dumps(chat_payload).encode("utf-8"),
				headers=headers,
				method="POST",
			)
			with urlopen(upstream_req, timeout=300) as resp:
				upstream = json.loads(resp.read().decode("utf-8"))

			response, output_events = build_response_from_chat(payload, upstream, prior_messages)

			wants_stream = bool(payload.get("stream")) or self.headers.get("Accept", "") == "text/event-stream"
			if not wants_stream:
				return self._send_json(200, response)

			self.send_response(200)
			self.send_header("Content-Type", "text/event-stream")
			self.send_header("Cache-Control", "no-cache")
			self.send_header("Connection", "close")
			self.end_headers()

			self._send_sse({"type": "response.created", "response": {**response, "status": "in_progress", "output": []}})
			self._send_sse({"type": "response.in_progress", "response": {**response, "status": "in_progress", "output": []}})
			for event in output_events:
				self._send_sse(event)
			self._send_sse({"type": "response.completed", "response": response})
		except HTTPError as exc:
			self._send_json(exc.code, {"error": "upstream_error", "detail": exc.read().decode("utf-8", "replace")})
		except URLError as exc:
			self._send_json(502, {"error": "bad_gateway", "detail": str(exc.reason)})
		except Exception as exc:
			self._send_json(500, {"error": "internal_error", "detail": str(exc)})


class ResponsesProxy:
	"""Manages a local Responses API → Chat Completions proxy for Codex CLI.

	Usage::

		proxy = ResponsesProxy(
			backend_url="https://api.minimax.io/v1",
			api_key="sk-...",
		)
		proxy.start()
		# Use proxy.url as base_url in CodexOptions
		codex_tool(codex_options=CodexOptions(base_url=proxy.url, api_key="dummy"))
		# ...
		proxy.stop()

	Can also be used as a context manager::

		with ResponsesProxy(backend_url="...", api_key="...") as proxy:
			# proxy.url is available
			...
	"""

	def __init__(self, *, backend_url: str, api_key: str = "", port: int = 0):
		self._backend_url = backend_url.rstrip("/")
		self._api_key = api_key
		self._port = port
		self._server: ThreadingHTTPServer | None = None
		self._thread: threading.Thread | None = None

	@property
	def url(self) -> str:
		"""Return the proxy's base URL (e.g. http://127.0.0.1:54321)."""
		if not self._server:
			raise RuntimeError("Proxy not started")
		host, port = self._server.server_address
		return f"http://{host}:{port}"

	def start(self) -> str:
		"""Start the proxy in a background thread. Returns the proxy URL."""
		handler_class = type(
			"_ConfiguredHandler",
			(_ProxyHandler,),
			{"backend_url": self._backend_url, "api_key": self._api_key},
		)
		self._server = ThreadingHTTPServer(("127.0.0.1", self._port), handler_class)
		self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
		self._thread.start()
		return self.url

	def stop(self) -> None:
		"""Stop the proxy server."""
		if self._server:
			self._server.shutdown()
			self._server = None
		if self._thread:
			self._thread.join(timeout=5)
			self._thread = None

	def __enter__(self):
		self.start()
		return self

	def __exit__(self, *args):
		self.stop()


if __name__ == "__main__":
	import os
	from dotenv import load_dotenv
	load_dotenv()

	backend = os.environ.get("CODEX_SHIM_BACKEND_URL", "https://api.minimax.io/v1")
	api_key = os.environ.get("MINIMAX_API_KEY", "")

	with ResponsesProxy(backend_url=backend, api_key=api_key) as proxy:
		print(f"Proxy running at {proxy.url}")
		print(f"Backend: {backend}")
		print("Press Ctrl+C to stop")
		try:
			threading.Event().wait()
		except KeyboardInterrupt:
			print("\nStopping...")
