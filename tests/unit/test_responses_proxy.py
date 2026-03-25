"""Unit tests for the Responses API → Chat Completions proxy."""

from __future__ import annotations

import json

from lerim.runtime.responses_proxy import (
	convert_request_to_chat,
	build_response_from_chat,
)


def test_convert_simple_text_input():
	"""Simple text input should become a user message."""
	payload = {
		"model": "test-model",
		"input": [{"type": "message", "role": "user", "content": "hello"}],
	}
	chat, messages = convert_request_to_chat(payload)
	assert chat["model"] == "test-model"
	assert len(messages) == 1
	assert messages[0]["role"] == "user"
	assert messages[0]["content"] == "hello"


def test_convert_with_instructions():
	"""Instructions should become a system message at the top."""
	payload = {
		"model": "test-model",
		"instructions": "Be concise.",
		"input": [{"type": "message", "role": "user", "content": "hi"}],
	}
	chat, messages = convert_request_to_chat(payload)
	assert messages[0]["role"] == "system"
	assert messages[0]["content"] == "Be concise."
	assert messages[1]["role"] == "user"


def test_convert_string_input():
	"""Plain string in input array should become user message."""
	payload = {
		"model": "test-model",
		"input": ["hello world"],
	}
	chat, messages = convert_request_to_chat(payload)
	assert messages[0]["role"] == "user"
	assert messages[0]["content"] == "hello world"


def test_convert_developer_role():
	"""Developer role should be normalized to system."""
	payload = {
		"model": "test-model",
		"input": [{"type": "message", "role": "developer", "content": "system prompt"}],
	}
	chat, messages = convert_request_to_chat(payload)
	assert messages[0]["role"] == "system"


def test_convert_tool_calls():
	"""Function call + output should be converted correctly."""
	payload = {
		"model": "test-model",
		"input": [
			{"type": "message", "role": "user", "content": "do something"},
			{"type": "function_call", "call_id": "call_1", "name": "read_file", "arguments": '{"path": "test.txt"}'},
			{"type": "function_call_output", "call_id": "call_1", "output": "file contents"},
		],
	}
	chat, messages = convert_request_to_chat(payload)
	# user message + assistant with tool_calls + tool result
	assert messages[0]["role"] == "user"
	assert messages[1]["role"] == "assistant"
	assert messages[1]["tool_calls"][0]["function"]["name"] == "read_file"
	assert messages[2]["role"] == "tool"
	assert messages[2]["content"] == "file contents"


def test_convert_tools_normalized():
	"""Tools should be normalized to standard function format."""
	payload = {
		"model": "test-model",
		"input": [{"type": "message", "role": "user", "content": "hi"}],
		"tools": [
			{"type": "function", "function": {"name": "read_file", "description": "Read a file", "parameters": {"type": "object"}}},
		],
	}
	chat, messages = convert_request_to_chat(payload)
	assert len(chat["tools"]) == 1
	assert chat["tools"][0]["function"]["name"] == "read_file"


def test_build_response_text():
	"""Text-only response should produce message output."""
	upstream = {
		"choices": [{"message": {"content": "Hello!", "role": "assistant"}}],
		"usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
	}
	request_payload = {"model": "test-model"}
	response, events = build_response_from_chat(request_payload, upstream, [])

	assert response["status"] == "completed"
	assert len(response["output"]) == 1
	assert response["output"][0]["type"] == "message"
	assert response["output"][0]["content"][0]["text"] == "Hello!"
	assert response["usage"]["input_tokens"] == 10
	assert response["usage"]["output_tokens"] == 5


def test_build_response_tool_calls():
	"""Tool call response should produce function_call output items."""
	upstream = {
		"choices": [{
			"message": {
				"content": None,
				"role": "assistant",
				"tool_calls": [{
					"id": "call_abc",
					"type": "function",
					"function": {"name": "read_file", "arguments": '{"path": "test.txt"}'},
				}],
			},
		}],
		"usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
	}
	request_payload = {"model": "test-model"}
	response, events = build_response_from_chat(request_payload, upstream, [])

	assert len(response["output"]) == 1
	assert response["output"][0]["type"] == "function_call"
	assert response["output"][0]["name"] == "read_file"
	assert response["output"][0]["arguments"] == '{"path": "test.txt"}'


def test_build_response_mixed_text_and_tools():
	"""Response with both tool calls and text should produce both output types."""
	upstream = {
		"choices": [{
			"message": {
				"content": "Done!",
				"role": "assistant",
				"tool_calls": [{
					"id": "call_xyz",
					"type": "function",
					"function": {"name": "write_file", "arguments": '{}'},
				}],
			},
		}],
		"usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
	}
	request_payload = {"model": "test-model"}
	response, events = build_response_from_chat(request_payload, upstream, [])

	types = [item["type"] for item in response["output"]]
	assert "function_call" in types
	assert "message" in types
