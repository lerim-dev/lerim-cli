"""Adapter wrapper with field-name normalization and per-call retry.

Ported from stanfordnlp/dspy PR #8050 (approved, unmerged as of 2026-04).
Drop this module when DSPy merges RetryAdapter into the main package.

Two layers of defense against LLM formatting errors:
1. Normalize known field-name mismatches (e.g. <tool_args> -> <next_tool_args>)
2. On remaining parse failures, retry with error feedback injected into prompt
"""

from __future__ import annotations

import logging
import re
from typing import Any

import dspy
from dspy.utils.exceptions import AdapterParseError

logger = logging.getLogger(__name__)


# Known field-name mismatches that LLMs produce for DSPy ReAct output fields.
# See: https://github.com/stanfordnlp/dspy/issues/8377
_FIELD_ALIASES: dict[str, str] = {
	"thought": "next_thought",
	"tool_name": "next_tool_name",
	"tool_args": "next_tool_args",
	"tool_name_name": "next_tool_name",
	"name_tool_args": "next_tool_args",
}


def _normalize_xml_field_names(
	text: str,
	output_fields: set[str],
) -> str:
	"""Replace known alias XML tags with canonical field names.

	Handles two patterns:
	1. Static aliases: <tool_args> -> <next_tool_args>
	2. Numbered tags from trajectory contamination:
	   <tool_name_1> -> <next_tool_name> (first match only)

	Only replaces when:
	- The canonical name IS an expected output field
	- The alias name is NOT an expected output field (avoids clobbering)
	"""
	# Layer 1: static aliases (tool_args -> next_tool_args, etc.)
	for alias, canonical in _FIELD_ALIASES.items():
		if canonical in output_fields and alias not in output_fields:
			text = re.sub(
				rf"<{alias}>(.*?)</{alias}>",
				rf"<{canonical}>\1</{canonical}>",
				text,
				flags=re.DOTALL,
			)

	# Layer 2: numbered tags from trajectory contamination.
	# The trajectory uses <tool_name_0>, <tool_args_0>, <thought_0>, etc.
	# The model continues the pattern: <tool_name_1>, <tool_args_1>, ...
	# We take only the FIRST numbered match (one tool per ReAct iteration).
	_NUMBERED_PATTERNS = {
		"tool_name": "next_tool_name",
		"tool_args": "next_tool_args",
		"thought": "next_thought",
	}
	for prefix, canonical in _NUMBERED_PATTERNS.items():
		if canonical in output_fields:
			pattern = rf"<{prefix}_(\d+)>(.*?)</{prefix}_\1>"
			match = re.search(pattern, text, re.DOTALL)
			if match:
				replacement = f"<{canonical}>{match.group(2)}</{canonical}>"
				text = text[:match.start()] + replacement + text[match.end():]

	return text


def _add_retry_fields(signature: type) -> type:
	"""Append previous_response and error_message InputFields to signature.

	Based on create_signature_for_retry() from dspy PR #8050.
	"""
	return (
		signature
		.append(
			"previous_response",
			dspy.InputField(
				desc=(
					"Your previous response that failed to parse. "
					"Avoid the same formatting mistake."
				),
			),
			type_=str,
		)
		.append(
			"error_message",
			dspy.InputField(
				desc=(
					"Parsing error for the previous response. "
					"Follow the XML tag instructions exactly to fix it."
				),
			),
			type_=str,
		)
	)


def _build_error_feedback(
	error: AdapterParseError,
	output_fields: set[str],
) -> str:
	"""Build an actionable error message listing the exact XML tags expected."""
	tag_lines = "\n".join(f"<{f}>...</{f}>" for f in sorted(output_fields))
	original = str(error)
	# Truncate verbose errors to keep the prompt compact.
	if len(original) > 300:
		original = original[:300] + "..."
	return (
		"Your response could not be parsed. "
		"You must produce these XML tags exactly:\n"
		f"{tag_lines}\n"
		"Do not use any other tag names. "
		f"Previous error: {original}"
	)


class RetryAdapter:
	"""Adapter wrapper that retries on parse failure with error feedback.

	Wraps a main adapter (typically dspy.XMLAdapter). On AdapterParseError,
	injects the failed LLM response and error message as extra input fields
	and retries, so the LLM can see its mistake and correct it.

	The retry happens transparently within a single ReAct iteration —
	the ReAct loop never sees the error and no trajectory work is lost.

	Usage::

		adapter = RetryAdapter(dspy.XMLAdapter(), max_retries=2)
		with dspy.context(adapter=adapter):
			prediction = agent()
	"""

	def __init__(self, main_adapter: Any, max_retries: int = 2) -> None:
		self.main_adapter = main_adapter
		self.max_retries = max_retries
		# Disable ChatAdapter -> JSONAdapter implicit fallback.
		# It sends contradictory XML-in-JSON prompts and wastes an LLM call.
		if hasattr(main_adapter, "use_json_adapter_fallback"):
			main_adapter.use_json_adapter_fallback = False

	def __call__(
		self,
		lm: Any,
		lm_kwargs: dict[str, Any],
		signature: type,
		demos: list[dict[str, Any]],
		inputs: dict[str, Any],
	) -> list[dict[str, Any]]:
		"""Call the main adapter with normalization and retry on parse failure."""
		# First attempt — normal call through the main adapter.
		try:
			return self.main_adapter(lm, lm_kwargs, signature, demos, inputs)
		except AdapterParseError as err:
			last_error = err

		# Layer 1: try normalizing known field-name mismatches in the response.
		output_fields = set(signature.output_fields.keys())
		normalized = _normalize_xml_field_names(
			last_error.lm_response or "", output_fields,
		)
		if normalized != (last_error.lm_response or ""):
			try:
				value = self.main_adapter.parse(signature, normalized)
				logger.info(
					"RetryAdapter: recovered via field-name normalization",
				)
				return [value]
			except (AdapterParseError, Exception):
				pass  # normalization wasn't enough, fall through to retry

		# Layer 2: retry with error feedback injected into prompt.
		logger.info(
			"RetryAdapter: parse failed, retrying with error feedback "
			"(%d retries available)",
			self.max_retries,
		)

		retry_signature = _add_retry_fields(signature)
		retry_inputs = {**inputs}
		retry_inputs["previous_response"] = last_error.lm_response or ""
		retry_inputs["error_message"] = _build_error_feedback(
			last_error, output_fields,
		)

		for attempt in range(1, self.max_retries + 1):
			try:
				return self.main_adapter(
					lm, lm_kwargs, retry_signature, demos, retry_inputs,
				)
			except AdapterParseError as exc:
				# Try normalization on the retry response too.
				retry_normalized = _normalize_xml_field_names(
					exc.lm_response or "", output_fields,
				)
				if retry_normalized != (exc.lm_response or ""):
					try:
						value = self.main_adapter.parse(
							signature, retry_normalized,
						)
						logger.info(
							"RetryAdapter: recovered via normalization on "
							"retry %d/%d",
							attempt,
							self.max_retries,
						)
						return [value]
					except (AdapterParseError, Exception):
						pass

				logger.warning(
					"RetryAdapter: retry %d/%d failed: %s",
					attempt,
					self.max_retries,
					str(exc)[:120],
				)
				last_error = exc
				retry_inputs["previous_response"] = exc.lm_response or ""
				retry_inputs["error_message"] = _build_error_feedback(
					exc, output_fields,
				)

		raise last_error

	def __getattr__(self, name: str) -> Any:
		"""Delegate all other attribute access to the main adapter."""
		return getattr(self.main_adapter, name)
