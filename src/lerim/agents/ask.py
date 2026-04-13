"""Ask agent: search memories and answer questions with citations.

PydanticAI implementation that reads memory files via the shared tool surface.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.models import Model
from pydantic_ai.usage import UsageLimits

from lerim.agents.tools import ExtractDeps, read, scan


# ---------------------------------------------------------------------------
# Input formatter for ask hints
# ---------------------------------------------------------------------------


def format_ask_hints(
	hits: list[dict[str, Any]],
	context_docs: list[dict[str, Any]],
) -> str:
	"""Format pre-fetched hits and context docs into a hints string."""
	context_lines = [
		(
			f"- [{fm.get('type', '?')}] {fm.get('name', '?')}: "
			f"{fm.get('description', '')} :: {str(fm.get('body', '')).strip()[:260]}"
		)
		for fm in hits
	]
	context_block = "\n".join(context_lines) or "(no relevant memories)"

	context_doc_lines = []
	for row in context_docs:
		doc_id = str(row.get("doc_id") or "")
		title = str(row.get("title") or "")
		body = str(row.get("body") or "").strip()
		snippet = " ".join(body.split())[:260]
		context_doc_lines.append(f"- {doc_id}: {title} :: {snippet}")
	context_doc_block = (
		"\n".join(context_doc_lines)
		if context_doc_lines
		else "(no context docs loaded)"
	)

	return f"""\
Pre-fetched hints (may be incomplete -- use scan() for full manifest):
{context_block}

Context docs:
{context_doc_block}"""


ASK_SYSTEM_PROMPT = """\
<role>You are a memory query agent. You answer user questions by searching
and reading the memory store.</role>

<task>Find relevant memories, read them, and answer the question with
evidence and citations.</task>

<context>
Memory layout:
- {type}_{topic}.md -- memory files (feedback_, project_, user_, reference_)
- summaries/*.md -- session summaries (date-prefixed)
- index.md -- semantic index organized by section
Each memory file has YAML frontmatter (name, description, type) and markdown body.
</context>

<steps>
<step name="scan">Call scan() to see all memories (filename, description,
modified time). Filenames encode type and topic.</step>
<step name="read">Based on the question and descriptions, call read() on
relevant memories.</step>
<step name="answer">Answer with evidence, citing the filenames you used.</step>
</steps>

<completeness_contract>
If relevant memories exist, cite them in your answer.
If no relevant memories exist, say so clearly.
</completeness_contract>
"""


class AskResult(BaseModel):
	"""Structured output for the ask flow."""

	answer: str = Field(description="Answer text with filename citations when available")


def build_ask_agent(model: Model) -> Agent[ExtractDeps, AskResult]:
	"""Build Ask agent with read-only memory tools."""
	return Agent(
		model,
		deps_type=ExtractDeps,
		output_type=AskResult,
		system_prompt=ASK_SYSTEM_PROMPT,
		tools=[read, scan],
		retries=5,
		output_retries=2,
	)


def run_ask(
	*,
	memory_root: Path,
	model: Model,
	question: str,
	hints: str = "",
	request_limit: int = 30,
	return_messages: bool = False,
):
	"""Run the ask agent.

	Returns AskResult, or (AskResult, list[ModelMessage]) when return_messages=True.
	"""
	agent = build_ask_agent(model)
	deps = ExtractDeps(memory_root=memory_root)
	prompt = (
		f"Question:\n{question.strip()}\n\n"
		f"Hints:\n{hints.strip() or '(no hints)'}"
	)
	result = agent.run_sync(
		prompt,
		deps=deps,
		usage_limits=UsageLimits(request_limit=max(1, int(request_limit))),
	)
	if return_messages:
		return result.output, list(result.all_messages())
	return result.output
