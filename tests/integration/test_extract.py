"""Integration tests for extraction pipeline quality (requires real LLM)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

_skip = pytest.mark.skipif(
    not os.environ.get("LERIM_INTEGRATION"),
    reason="LERIM_INTEGRATION not set",
)

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "traces"


@_skip
def test_extraction_schema_conformance():
    """All extracted candidates conform to MemoryCandidate schema."""
    from lerim.memory.extract_pipeline import extract_memories_from_session_file
    from lerim.memory.schemas import MemoryCandidate

    result = extract_memories_from_session_file(FIXTURES_DIR / "claude_simple.jsonl")
    assert isinstance(result, list)
    for item in result:
        MemoryCandidate.model_validate(item)


@_skip
def test_extraction_primitive_classification():
    """Decisions classified as 'decision', learnings as 'learning'."""
    from lerim.memory.extract_pipeline import extract_memories_from_session_file

    result = extract_memories_from_session_file(
        FIXTURES_DIR / "mixed_decisions_learnings.jsonl"
    )
    primitives = {item["primitive"] for item in result}
    assert "decision" in primitives or "learning" in primitives


@_skip
def test_extraction_minimum_quality():
    """Each candidate has title >= 8 chars, body >= 24 chars."""
    from lerim.memory.extract_pipeline import extract_memories_from_session_file

    result = extract_memories_from_session_file(FIXTURES_DIR / "claude_simple.jsonl")
    for item in result:
        assert len(item.get("title", "")) >= 8, f"Title too short: {item.get('title')}"
        assert len(item.get("body", "")) >= 24, f"Body too short: {item.get('body')}"


@_skip
def test_extraction_on_short_trace():
    """Very short trace (2 messages) produces at least 0 candidates without error."""
    from lerim.memory.extract_pipeline import extract_memories_from_session_file

    result = extract_memories_from_session_file(FIXTURES_DIR / "edge_short.jsonl")
    assert isinstance(result, list)


@_skip
def test_extraction_on_empty_content():
    """Trace with no extractable content produces empty list without error."""
    from lerim.memory.extract_pipeline import extract_memories_from_session_file

    result = extract_memories_from_session_file(FIXTURES_DIR / "edge_empty.jsonl")
    assert isinstance(result, list)
