"""Unit tests for memory candidate Pydantic schemas."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from lerim.memory.schemas import MemoryCandidate


def test_memory_candidate_valid_decision():
    """Valid decision candidate passes validation."""
    c = MemoryCandidate(
        primitive="decision",
        title="Use JWT for auth",
        body="We chose JWT with HS256.",
        confidence=0.9,
        tags=["auth"],
    )
    assert c.primitive == "decision"
    assert c.title == "Use JWT for auth"


def test_memory_candidate_valid_learning():
    """Valid learning candidate with kind passes validation."""
    c = MemoryCandidate(
        primitive="learning",
        kind="pitfall",
        title="Queue must be atomic",
        body="Always use atomic claims.",
        confidence=0.8,
        tags=["queue"],
    )
    assert c.kind == "pitfall"


def test_memory_candidate_invalid_primitive():
    """primitive='summary' -> ValidationError."""
    with pytest.raises(ValidationError):
        MemoryCandidate(
            primitive="summary",
            title="Bad type",
            body="Should fail",
        )


def test_memory_candidate_confidence_bounds():
    """confidence outside [0,1] -> ValidationError."""
    with pytest.raises(ValidationError):
        MemoryCandidate(
            primitive="decision",
            title="Test",
            body="Test",
            confidence=1.5,
        )
    with pytest.raises(ValidationError):
        MemoryCandidate(
            primitive="decision",
            title="Test",
            body="Test",
            confidence=-0.1,
        )


def test_memory_candidate_empty_title():
    """Empty title -> still passes (Pydantic allows empty str by default)."""
    # MemoryCandidate uses plain str, so empty is technically valid
    c = MemoryCandidate(primitive="decision", title="", body="content")
    assert c.title == ""


def test_memory_candidate_tags_are_list():
    """Tags field must be list of strings."""
    c = MemoryCandidate(
        primitive="decision",
        title="Test",
        body="Body",
        tags=["a", "b", "c"],
    )
    assert isinstance(c.tags, list)
    assert all(isinstance(t, str) for t in c.tags)


def test_memory_candidate_json_schema():
    """model_json_schema() produces valid JSON Schema dict."""
    schema = MemoryCandidate.model_json_schema()
    assert isinstance(schema, dict)
    assert "title" in schema
    assert "properties" in schema
    assert "primitive" in schema["properties"]
    assert "body" in schema["properties"]
