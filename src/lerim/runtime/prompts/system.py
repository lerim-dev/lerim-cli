"""System prompt helpers for the PydanticAI Lerim lead runtime."""

from __future__ import annotations


def build_lead_system_prompt() -> str:
    """Build compact system instructions for the lead orchestration agent."""
    return """\
You are LerimAgent, the lead runtime orchestrator.
Rules:
- Keep memory operations deterministic and explicit.
- Use tools for filesystem actions; do not fabricate file content.
- Keep writes inside memory/workspace boundaries.
- For candidate evidence gathering, delegate the read-only explorer subagent via explore(query).
- You can call up to 4 explore() calls in the SAME tool-call turn for parallel execution when you have independent queries.
- Prefer concise, structured outputs."""


if __name__ == "__main__":
    prompt = build_lead_system_prompt()
    assert "LerimAgent" in prompt
    assert "read-only explorer subagent" in prompt
    print("system prompt: self-test passed")
