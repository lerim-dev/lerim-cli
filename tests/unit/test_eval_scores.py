"""Tests for eval scoring utilities and judge output parsing."""

from __future__ import annotations

import json
from pathlib import Path

from evals.judge import _parse_agent_output, build_judge_prompt
from evals.scores import (
    EvalScore,
    check_extraction_schema,
    check_summarization_fields,
    check_word_limits,
    compute_composite,
)


# --- compute_composite ---


def test_composite_perfect_scores() -> None:
    """Perfect scores produce composite of 1.0."""
    assert compute_composite(1.0, 1.0, 1.0) == 1.0


def test_composite_zero_scores() -> None:
    """Zero scores produce composite of 0.0."""
    assert compute_composite(0.0, 0.0, 0.0) == 0.0


def test_composite_weighted_average() -> None:
    """Composite uses 40/35/25 weighting."""
    result = compute_composite(0.8, 0.6, 0.4)
    expected = 0.8 * 0.4 + 0.6 * 0.35 + 0.4 * 0.25
    assert abs(result - expected) < 1e-9


# --- check_extraction_schema ---


def test_extraction_schema_valid() -> None:
    """Valid MemoryCandidate dicts pass schema check."""
    assert check_extraction_schema(
        [{"primitive": "decision", "title": "T", "body": "B"}]
    )


def test_extraction_schema_invalid_fields() -> None:
    """Dicts missing required fields fail schema check."""
    assert not check_extraction_schema([{"bad": "data"}])


def test_extraction_schema_empty_list() -> None:
    """Empty list fails schema check."""
    assert not check_extraction_schema([])


def test_extraction_schema_non_list() -> None:
    """Non-list input fails schema check."""
    assert not check_extraction_schema("not a list")  # type: ignore[arg-type]


# --- check_summarization_fields ---


def test_summarization_fields_valid() -> None:
    """Dict with all required fields passes."""
    assert check_summarization_fields({
        "title": "t",
        "description": "d",
        "user_intent": "u",
        "session_narrative": "s",
        "coding_agent": "c",
    })


def test_summarization_fields_missing() -> None:
    """Dict missing required fields fails."""
    assert not check_summarization_fields({"title": "t"})


def test_summarization_fields_non_dict() -> None:
    """Non-dict input fails."""
    assert not check_summarization_fields("not a dict")  # type: ignore[arg-type]


# --- check_word_limits ---


def test_word_limits_within_bounds() -> None:
    """Content within limits passes."""
    assert check_word_limits({
        "user_intent": "a " * 150,
        "session_narrative": "b " * 200,
    })


def test_word_limits_intent_exceeded() -> None:
    """user_intent over 150 words fails."""
    assert not check_word_limits({
        "user_intent": "a " * 151,
        "session_narrative": "b",
    })


def test_word_limits_narrative_exceeded() -> None:
    """session_narrative over 200 words fails."""
    assert not check_word_limits({
        "user_intent": "a",
        "session_narrative": "b " * 201,
    })


# --- EvalScore ---


def test_eval_score_construction() -> None:
    """EvalScore dataclass constructs with required and default fields."""
    score = EvalScore(trace="test.jsonl", schema_ok=True, has_candidates=True)
    assert score.trace == "test.jsonl"
    assert score.schema_ok is True
    assert score.composite == 0.0
    assert score.candidate_count == 0


# --- _parse_agent_output ---


def test_parse_agent_output_direct_json() -> None:
    """Direct JSON string is parsed correctly."""
    assert _parse_agent_output("codex", '{"completeness": 0.8}') == {
        "completeness": 0.8,
    }


def test_parse_agent_output_claude_wrapper() -> None:
    """Claude wrapper JSON (result field) is unwrapped and parsed."""
    wrapper = json.dumps({"result": '{"completeness": 0.9}'})
    assert _parse_agent_output("claude", wrapper) == {"completeness": 0.9}


def test_parse_agent_output_markdown_code_block() -> None:
    """JSON inside markdown code block is extracted."""
    md = 'Some text\n```json\n{"clarity": 0.7}\n```\nmore text'
    assert _parse_agent_output("codex", md) == {"clarity": 0.7}


# --- build_judge_prompt ---


def test_build_judge_prompt(tmp_path: Path) -> None:
    """Template placeholders are replaced with trace path and output."""
    template = tmp_path / "prompt.md"
    template.write_text("Evaluate {trace_path}\nOutput: {output}", encoding="utf-8")

    result = build_judge_prompt(template, Path("/tmp/trace.jsonl"), '{"data": 1}')
    assert "/tmp/trace.jsonl" in result
    assert '{"data": 1}' in result
