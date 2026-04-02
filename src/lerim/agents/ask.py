"""Ask agent: search memories and answer questions.

question -> dspy.ReAct(AskSignature, tools) -> answer with citations.
"""

from __future__ import annotations

from typing import Any

import dspy

from lerim.agents.context import RuntimeContext
from lerim.agents.tools import make_ask_tools


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
Pre-fetched hints (may be incomplete -- use scan_memory_manifest for more):
{context_block}

Context docs:
{context_doc_block}"""


class AskSignature(dspy.Signature):
    """Answer the user question using your memory tools.

    Steps:
    1. Call scan_memory_manifest() to see all available memories
    2. Based on the question, decide which memory files to read
    3. Call read_file() on relevant memories
    4. Answer with evidence and file path citations
    5. If no relevant memories exist, say so clearly

    Memory layout (under memory_root):
    - *.md -- memory files (types: user, feedback, project, reference)
    - summaries/YYYYMMDD/HHMMSS/*.md -- session summaries
    Each file: YAML frontmatter (name, description, type) + markdown body.

    Available tools:
    - scan_memory_manifest() -- compact list of all memories
    - list_files() -- browse directories
    - read_file(path) -- get full content of a specific file
    """

    question: str = dspy.InputField(
        desc="The user's question to answer"
    )
    memory_root: str = dspy.InputField(
        desc="Path to the memory root directory"
    )
    hints: str = dspy.InputField(
        desc="Pre-fetched context (may be empty)"
    )
    answer: str = dspy.OutputField(
        desc="Answer citing memory file paths"
    )


class AskAgent(dspy.Module):
    """DSPy ReAct module for the ask flow. Independently optimizable."""

    def __init__(self, ctx: RuntimeContext):
        super().__init__()
        self.react = dspy.ReAct(
            AskSignature,
            tools=make_ask_tools(ctx),
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
