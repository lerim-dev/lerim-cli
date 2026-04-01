"""Ask agent: search memories and answer questions.

question -> dspy.ReAct(AskSignature, tools) -> answer with citations.
"""

from __future__ import annotations

from typing import Any

import dspy

from lerim.runtime.context import RuntimeContext
from lerim.runtime.tools import bind_ask_tools


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
			f"- {fm.get('id', '?')} conf={fm.get('confidence', '?')}: "
			f"{fm.get('title', '?')} :: {str(fm.get('_body', '')).strip()[:260]}"
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
Pre-fetched hints (may be incomplete -- use memory_search for more):
{context_block}

Context docs:
{context_doc_block}"""


class AskSignature(dspy.Signature):
	"""Answer the user question using your memory search tools.

	- Use memory_search(mode="keyword") to find relevant memories in the memory root.
	- Search project-first, then global fallback.
	- Use read_file to retrieve full content when needed.
	- Return evidence with file paths.
	- If no relevant memories exist, say that clearly.
	- Cite memory ids you used.

	Memory layout (under memory_root):
	- decisions/*.md -- architecture and design decisions
	- learnings/*.md -- insights, procedures, pitfalls, preferences
	- summaries/YYYYMMDD/HHMMSS/*.md -- session summaries
	Each file: YAML frontmatter (id, title, confidence, tags, kind, created)
	+ markdown body.

	Available tools:
	- memory_search(mode="keyword", query="...") to search across all memory files
	- list_files(path="decisions/") or list_files(path="learnings/") to browse
	  directories
	- read_file(path="...") to get full content of a specific file

	The hints input contains pre-fetched memory search results (may be incomplete --
	use memory_search for more). Always search beyond the pre-fetched hints to
	ensure complete answers.
	"""

	question: str = dspy.InputField(
		desc="The user's question to answer"
	)
	memory_root: str = dspy.InputField(
		desc="Path to the memory root directory, or empty if not set"
	)
	hints: str = dspy.InputField(
		desc="Pre-fetched memory search hints (may be incomplete)"
	)
	answer: str = dspy.OutputField(
		desc="Answer citing memory file paths and IDs"
	)


class AskAgent(dspy.Module):
	"""DSPy ReAct module for the ask flow. Independently optimizable."""

	def __init__(self, ctx: RuntimeContext):
		super().__init__()
		self.react = dspy.ReAct(
			AskSignature,
			tools=bind_ask_tools(ctx),
			max_iters=ctx.config.lead_role.max_iters_ask,
		)

	def forward(
		self,
		question: str,
		memory_root: str,
		hints: str,
	) -> dspy.Prediction:
		return self.react(
			question=question,
			memory_root=memory_root,
			hints=hints,
		)
