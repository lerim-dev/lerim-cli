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
	"""Answer the user question using your memory tools.

	Steps:
	1. Call scan() to see all available memories (returns filename,
	   description, modified time). Filenames encode the type and topic
	   (e.g. feedback_use_tabs.md, project_dspy_migration.md).
	2. Based on the question and descriptions, decide which files to read
	3. Call read() on relevant memories (use filenames from scan)
	4. Answer with evidence and cite the filenames you used
	5. If no relevant memories exist, say so clearly

	Memory layout:
	- {type}_{topic}.md -- memory files (feedback_, project_, user_, reference_)
	- summaries/*.md -- session summaries (date-prefixed)
	- index.md -- semantic index organized by section
	Each memory file: YAML frontmatter (name, description, type) + markdown body.
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
		with dspy.context(adapter=dspy.XMLAdapter()):
			return self.react(
				question=question,
				hints=hints,
			)
