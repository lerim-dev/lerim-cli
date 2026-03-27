"""Shared schemas for memory operations (extraction, storage, etc.).

This module contains Pydantic models used across multiple memory modules
to avoid circular import issues.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class MemoryCandidate(BaseModel):
    """One extracted memory candidate from a transcript."""

    primitive: Literal["decision", "learning"] = Field(
        description="Memory type: decision or learning. Never summary."
    )
    kind: str | None = Field(
        default=None,
        description="Subtype for learnings: insight, procedure, friction, pitfall, or preference. Must be null for decisions.",
    )
    title: str = Field(description="Short memory title.")
    body: str = Field(description="Memory content in plain language. Must add substantive information beyond the title.")
    confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Confidence score 0-1. 0.9+ = explicitly stated/decided. 0.7-0.8 = strongly implied or accepted without objection. 0.5-0.6 = inferred. Below 0.5 = should not be extracted.",
    )
    source_speaker: Literal["user", "agent", "both"] = Field(
        default="both",
        description="Who originated this: 'user' = user stated/decided. 'agent' = agent chose during implementation. 'both' = emerged from dialog or agent decided and user accepted.",
    )
    durability: Literal["permanent", "project", "session"] = Field(
        default="project",
        description="Expected lifespan. permanent = preferences/identity. project = codebase-specific. session = ephemeral (should usually NOT be extracted).",
    )
    tags: list[str] = Field(
        default_factory=list,
        description="Group/cluster labels for this memory. No limit.",
    )
    outcome: Literal["worked", "failed", "unknown"] | None = Field(
        default=None,
        description="Whether this approach was validated (worked), tried and didn't work (failed), or unclear (unknown). Helps contradiction detection.",
    )


if __name__ == "__main__":
    candidate = MemoryCandidate(
        primitive="decision",
        title="Test",
        body="Test content",
        confidence=0.9,
        tags=["test"],
        source_speaker="both",
        durability="permanent",
    )
    print(f"MemoryCandidate schema: {candidate.model_json_schema()['title']}")
    print("schemas: self-test passed")
