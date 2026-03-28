"""Shared schemas for memory operations (extraction, storage, etc.).

This module contains Pydantic models used across multiple memory modules
to avoid circular import issues.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

# Learning subtypes; models sometimes emit these as `primitive`. Keep in sync with
# VALID_KINDS in lerim.runtime.oai_tools.
_LEARNING_KINDS_MISUSED_AS_PRIMITIVE = frozenset(
    {"insight", "procedure", "friction", "pitfall", "preference"}
)


class MemoryCandidate(BaseModel):
    """One extracted memory candidate from a transcript."""

    primitive: Literal["decision", "learning"] = Field(
        description="Memory type: decision or learning. Never summary."
    )
    kind: str | None = Field(
        default=None,
        description="Subtype for learnings: insight, procedure, friction, pitfall, or preference. Must be null for decisions.",
    )
    title: str = Field(description="Short descriptive title starting with a verb or noun phrase. Format: 'Use X for Y', 'Switch to X', 'X causes Y'. Max 10 words. Must be specific enough to identify the topic without reading the body.")
    body: str = Field(description="Memory content in plain language. Must add substantive information beyond the title — include the WHY (rationale), WHAT was considered (alternatives), and CONTEXT (when this applies). Minimum 2 sentences.")
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

    @model_validator(mode="before")
    @classmethod
    def coerce_learning_kind_misused_as_primitive(cls, data: Any) -> Any:
        """Map mistaken primitive values (learning subtypes) to primitive=learning + kind."""
        if not isinstance(data, dict):
            return data
        prim = data.get("primitive")
        if prim not in _LEARNING_KINDS_MISUSED_AS_PRIMITIVE:
            return data
        out = dict(data)
        out["primitive"] = "learning"
        kind = out.get("kind")
        if kind is None or (isinstance(kind, str) and not kind.strip()):
            out["kind"] = prim
        return out


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
