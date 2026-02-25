"""Chat prompt builder and auth-error helper for LerimAgent chat flow."""

from __future__ import annotations

from typing import Any


def build_chat_prompt(
    question: str,
    hits: list[dict[str, Any]],
    context_docs: list[dict[str, Any]],
    memory_root: str | None = None,
) -> str:
    """Build the final agent prompt with memory/context evidence blocks."""
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

    memory_guidance = ""
    if memory_root:
        memory_guidance = f"""
Memory location: {memory_root}
Structure: decisions/*.md and learnings/*.md — YAML frontmatter + markdown body.
Frontmatter fields: id, title, created, updated, confidence, tags, kind (learnings only).
Use the memory-explorer subagent for efficient two-phase retrieval (frontmatter scan first, then full read for relevant memories only).
"""

    return f"""\
Answer the user question using the memory evidence below.
Retrieval contract:
- Lead handles retrieval strategy.
- Delegate the memory-explorer subagent for two-phase memory retrieval.
- You can call up to 4 explore() calls in the SAME tool-call turn for parallel execution when you have independent queries.
- Search project-first, then global fallback.
- Return evidence with file paths and line refs.
- Use explicit ids/slugs only in related references (no wikilink syntax).
If memory is missing or uncertain, say that clearly.
Cite learning ids and context doc ids you used.
{memory_guidance}
Question:
{question}

Memory evidence:
{context_block}

Context docs (loaded only if needed):
{context_doc_block}
"""


def looks_like_auth_error(response: str) -> bool:
    """Return whether response text indicates authentication failure."""
    text = str(response or "").lower()
    return (
        "failed to authenticate" in text
        or "authentication_error" in text
        or "oauth token has expired" in text
        or "invalid api key" in text
        or "unauthorized" in text
    )


if __name__ == "__main__":
    prompt = build_chat_prompt(
        "how to deploy",
        [
            {
                "id": "mem-1",
                "confidence": 0.9,
                "title": "Deploy tips",
                "_body": "Use CI.",
            }
        ],
        [{"doc_id": "doc-1", "title": "CI Setup", "body": "Configure pipelines."}],
    )
    assert "how to deploy" in prompt
    assert "mem-1" in prompt
    assert "doc-1" in prompt
    assert "memory-explorer" in prompt
    assert "Context docs (loaded only if needed)" in prompt

    # With memory_root — should include guidance
    prompt_mr = build_chat_prompt("test", [], [], memory_root="/tmp/test/memory")
    assert "Memory location: /tmp/test/memory" in prompt_mr
    assert "two-phase retrieval" in prompt_mr
    assert "frontmatter" in prompt_mr

    # Without memory_root — no guidance block
    prompt_no = build_chat_prompt("test", [], [])
    assert "Memory location" not in prompt_no

    assert looks_like_auth_error("Failed to authenticate with provider")
    assert looks_like_auth_error("authentication_error: invalid key")
    assert not looks_like_auth_error("All good")

    print("chat prompt: all self-tests passed")
