"""Ask agent: search memories and answer questions.

question -> dspy.ReAct(AskSignature, tools) -> answer with citations.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import dspy

from lerim.agents.tools import MemoryTools


# ---------------------------------------------------------------------------
# Input formatter for AskSignature.hints
# ---------------------------------------------------------------------------

def format_ask_hints(
	hits: list[dict[str, Any]],
	context_docs: list[dict[str, Any]],
) -> str:
	"""Format pre-fetched hits and context docs into a hints string.

	Returns a combined text block suitable for the AskSignature.hints field.
	"""
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


class AskSignature(dspy.Signature):
	"""
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

	question: str = dspy.InputField(
		desc="The user's question to answer"
	)
	hints: str = dspy.InputField(
		desc="Pre-fetched context (may be empty)"
	)
	answer: str = dspy.OutputField(
		desc="Answer citing memory filenames"
	)


class AskAgent(dspy.Module):
	"""DSPy ReAct module for the ask flow. Independently optimizable."""

	def __init__(self, memory_root: Path, max_iters: int = 30):
		super().__init__()
		self.tools = MemoryTools(memory_root=memory_root)
		self.react = dspy.ReAct(
			AskSignature,
			tools=[
				self.tools.read,
				self.tools.scan,
			],
			max_iters=max_iters,
		)

	def forward(
		self,
		question: str,
		hints: str,
	) -> dspy.Prediction:
		from lerim.agents.retry_adapter import RetryAdapter
		adapter = RetryAdapter(dspy.XMLAdapter())
		with dspy.context(adapter=adapter):
			return self.react(
				question=question,
				hints=hints,
			)
