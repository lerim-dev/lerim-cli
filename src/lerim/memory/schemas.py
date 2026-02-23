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
        description="Subtype: insight, procedure, friction, pitfall, or preference. Usually set when primitive=learning.",
    )
    title: str = Field(description="Short memory title.")
    body: str = Field(description="Memory content in plain language.")
    confidence: float | None = Field(
        default=None, ge=0.0, le=1.0, description="Confidence score from 0 to 1."
    )
    tags: list[str] = Field(
        default_factory=list,
        description="Group/cluster labels for this memory. No limit.",
    )


if __name__ == "__main__":
    candidate = MemoryCandidate(
        primitive="decision",
        title="Test",
        body="Test content",
        confidence=0.9,
        tags=["test"],
    )
    print(f"MemoryCandidate schema: {candidate.model_json_schema()['title']}")
    print("schemas: self-test passed")
