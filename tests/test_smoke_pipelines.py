"""Smoke tests for extraction and summarization pipelines (requires ollama)."""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.smoke

_skip = pytest.mark.skipif(
    not os.environ.get("LERIM_SMOKE"),
    reason="LERIM_SMOKE not set",
)


@_skip
def test_extraction_pipeline_loads_and_produces_output(tmp_path):
    """Extract pipeline runs on tiny input, returns list of MemoryCandidate-shaped dicts."""
    from lerim.memory.extract_pipeline import extract_memories_from_session_file

    trace = tmp_path / "tiny.jsonl"
    trace.write_text(
        '{"role":"user","content":"Decision: use Redis for caching."}\n'
        '{"role":"assistant","content":"Noted. I will configure Redis."}\n',
        encoding="utf-8",
    )
    result = extract_memories_from_session_file(trace)
    assert isinstance(result, list)
    for item in result:
        assert "primitive" in item
        assert "title" in item
        assert "body" in item


@_skip
def test_summarization_pipeline_loads_and_produces_output(tmp_path):
    """Summarize pipeline runs on tiny input, returns dict with required fields."""
    from lerim.memory.summarization_pipeline import summarize_trace_from_session_file

    trace = tmp_path / "tiny.jsonl"
    trace.write_text(
        '{"role":"user","content":"Set up logging for the project."}\n'
        '{"role":"assistant","content":"I configured structlog with JSON output."}\n',
        encoding="utf-8",
    )
    result = summarize_trace_from_session_file(trace)
    assert isinstance(result, dict)
    assert "title" in result
    assert "description" in result
    assert "user_intent" in result
    assert "session_narrative" in result


@_skip
def test_dspy_lm_configures_for_extract():
    """configure_dspy_lm('extract') configures without error."""
    from lerim.memory.utils import configure_dspy_lm

    configure_dspy_lm("extract")


@_skip
def test_dspy_lm_configures_for_summarize():
    """configure_dspy_lm('summarize') configures without error."""
    from lerim.memory.utils import configure_dspy_lm

    configure_dspy_lm("summarize")
