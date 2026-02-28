"""Integration tests for summarization pipeline quality (requires real LLM)."""

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
def test_summarization_all_fields_present():
    """Summary output has title, description, user_intent, session_narrative, date, time, coding_agent."""
    from lerim.memory.summarization_pipeline import summarize_trace_from_session_file

    result = summarize_trace_from_session_file(FIXTURES_DIR / "claude_simple.jsonl")
    for key in (
        "title",
        "description",
        "user_intent",
        "session_narrative",
        "date",
        "time",
        "coding_agent",
    ):
        assert key in result, f"Missing field: {key}"
        assert result[key], f"Empty field: {key}"


@_skip
def test_summarization_word_limits():
    """user_intent <= 150 words, session_narrative <= 200 words."""
    from lerim.memory.summarization_pipeline import summarize_trace_from_session_file

    result = summarize_trace_from_session_file(FIXTURES_DIR / "claude_simple.jsonl")
    user_intent_words = len(result.get("user_intent", "").split())
    narrative_words = len(result.get("session_narrative", "").split())
    assert user_intent_words <= 150, f"user_intent has {user_intent_words} words"
    assert narrative_words <= 200, f"session_narrative has {narrative_words} words"


@_skip
def test_summarization_coding_agent_detected():
    """coding_agent field reflects the actual agent platform."""
    from lerim.memory.summarization_pipeline import summarize_trace_from_session_file

    result = summarize_trace_from_session_file(
        FIXTURES_DIR / "claude_simple.jsonl",
        metadata={"agent_type": "claude"},
    )
    assert result.get("coding_agent"), "coding_agent is empty"


@_skip
def test_summarization_tags_are_relevant():
    """Tags list is non-empty and contains strings."""
    from lerim.memory.summarization_pipeline import summarize_trace_from_session_file

    result = summarize_trace_from_session_file(FIXTURES_DIR / "claude_simple.jsonl")
    tags = result.get("tags", [])
    assert isinstance(tags, list)
    if tags:
        assert all(isinstance(t, str) for t in tags)
