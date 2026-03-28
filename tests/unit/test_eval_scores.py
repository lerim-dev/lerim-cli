"""Tests for eval scoring utilities and judge output parsing."""

from __future__ import annotations

import json
import math
from pathlib import Path

from evals.judge import _parse_agent_output, build_judge_prompt
from evals.scores import (
	EvalScore,
	LerimBenchScore,
	_fuzzy_title_match,
	check_archive_precision,
	check_dedup_accuracy,
	check_extraction_assertions,
	check_extraction_schema,
	check_summarization_assertions,
	check_summarization_fields,
	check_word_limits,
	compute_composite,
	compute_lerim_bench_composite,
	compute_ndcg,
)


# --- compute_composite ---


def test_composite_perfect_scores() -> None:
    """Perfect scores produce composite of 1.0."""
    assert compute_composite(1.0, 1.0, 1.0) == 1.0


def test_composite_zero_scores() -> None:
    """Zero scores (including precision) produce composite of 0.0."""
    assert compute_composite(0.0, 0.0, 0.0, 0.0) == 0.0


def test_composite_weighted_average() -> None:
    """Composite uses precision 30%, completeness 25%, faithfulness 25%, clarity 20%."""
    result = compute_composite(0.8, 0.6, 0.4, 0.9)
    expected = 0.9 * 0.30 + 0.8 * 0.25 + 0.6 * 0.25 + 0.4 * 0.20
    assert abs(result - expected) < 1e-9


def test_composite_default_precision() -> None:
    """When precision is not passed, defaults to 1.0 for backward compatibility."""
    result = compute_composite(1.0, 1.0, 1.0)
    assert result == 1.0
    # With precision=1.0 default: 0.30 + 0.8*0.25 + 0.6*0.25 + 0.4*0.20 = 0.73
    result2 = compute_composite(0.8, 0.6, 0.4)
    expected = 1.0 * 0.30 + 0.8 * 0.25 + 0.6 * 0.25 + 0.4 * 0.20
    assert abs(result2 - expected) < 1e-9


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
    assert check_summarization_fields(
        {
            "title": "t",
            "description": "d",
            "user_intent": "u",
            "session_narrative": "s",
            "coding_agent": "c",
        }
    )


def test_summarization_fields_missing() -> None:
    """Dict missing required fields fails."""
    assert not check_summarization_fields({"title": "t"})


def test_summarization_fields_non_dict() -> None:
    """Non-dict input fails."""
    assert not check_summarization_fields("not a dict")  # type: ignore[arg-type]


# --- check_word_limits ---


def test_word_limits_within_bounds() -> None:
    """Content within limits passes."""
    assert check_word_limits(
        {
            "user_intent": "a " * 150,
            "session_narrative": "b " * 200,
        }
    )


def test_word_limits_intent_exceeded() -> None:
    """user_intent over 150 words fails."""
    assert not check_word_limits(
        {
            "user_intent": "a " * 151,
            "session_narrative": "b",
        }
    )


def test_word_limits_narrative_exceeded() -> None:
    """session_narrative over 200 words fails."""
    assert not check_word_limits(
        {
            "user_intent": "a",
            "session_narrative": "b " * 201,
        }
    )


# --- EvalScore ---


def test_eval_score_construction() -> None:
    """EvalScore dataclass constructs with required and default fields."""
    score = EvalScore(trace="test.jsonl", schema_ok=True, has_candidates=True)
    assert score.trace == "test.jsonl"
    assert score.schema_ok is True
    assert score.composite == 0.0
    assert score.candidate_count == 0


# --- LerimBenchScore ---


def test_lerim_bench_score_defaults() -> None:
    """LerimBenchScore defaults have sensible values."""
    score = LerimBenchScore()
    assert score.extraction_precision == 0.0
    assert score.scale_degradation == 1.0


def test_lerim_bench_composite_perfect() -> None:
    """Perfect LerimBench score produces composite of 1.0."""
    score = LerimBenchScore(
        extraction_precision=1.0,
        extraction_recall=1.0,
        dedup_accuracy=1.0,
        consolidation_quality=1.0,
        archive_precision=1.0,
        search_relevance=1.0,
        scale_degradation=1.0,
    )
    assert abs(compute_lerim_bench_composite(score) - 1.0) < 1e-9


def test_lerim_bench_composite_zero() -> None:
    """All-zero LerimBench score produces composite of 0.0."""
    score = LerimBenchScore(
        extraction_precision=0.0,
        extraction_recall=0.0,
        dedup_accuracy=0.0,
        consolidation_quality=0.0,
        archive_precision=0.0,
        search_relevance=0.0,
        scale_degradation=0.0,
    )
    assert compute_lerim_bench_composite(score) == 0.0


def test_lerim_bench_composite_weighted() -> None:
    """LerimBench composite uses correct weights."""
    score = LerimBenchScore(
        extraction_precision=0.9,
        extraction_recall=0.8,
        dedup_accuracy=0.85,
        consolidation_quality=0.7,
        archive_precision=0.95,
        search_relevance=0.75,
        scale_degradation=0.9,
    )
    expected = (
        0.9 * 0.20 + 0.8 * 0.20 + 0.85 * 0.15 + 0.7 * 0.15
        + 0.95 * 0.10 + 0.75 * 0.15 + 0.9 * 0.05
    )
    assert abs(compute_lerim_bench_composite(score) - expected) < 1e-9


# --- check_dedup_accuracy ---


def test_dedup_accuracy_all_correct() -> None:
    """All predictions match golden assertions."""
    golden = [
        {"candidate_title": "A", "expected_action": "add"},
        {"candidate_title": "B", "expected_action": "no_op"},
    ]
    preds = [
        {"candidate_title": "A", "action": "add"},
        {"candidate_title": "B", "action": "no_op"},
    ]
    assert check_dedup_accuracy(preds, golden) == 1.0


def test_dedup_accuracy_partial() -> None:
    """Half of predictions match golden assertions."""
    golden = [
        {"candidate_title": "A", "expected_action": "add"},
        {"candidate_title": "B", "expected_action": "no_op"},
    ]
    preds = [
        {"candidate_title": "A", "action": "add"},
        {"candidate_title": "B", "action": "add"},
    ]
    assert abs(check_dedup_accuracy(preds, golden) - 0.5) < 1e-9


def test_dedup_accuracy_empty_golden() -> None:
    """Empty golden returns 1.0 (nothing to check)."""
    assert check_dedup_accuracy([], []) == 1.0


def test_dedup_accuracy_missing_prediction() -> None:
    """Prediction missing for a golden item counts as incorrect."""
    golden = [{"candidate_title": "A", "expected_action": "add"}]
    preds: list[dict] = []
    assert check_dedup_accuracy(preds, golden) == 0.0


def test_dedup_accuracy_substring_match() -> None:
    """Fuzzy matching: golden title is a substring of prediction title."""
    golden = [{"candidate_title": "Use JWT", "expected_action": "add"}]
    preds = [{"candidate_title": "Use JWT with HS256 for auth", "action": "add"}]
    assert check_dedup_accuracy(preds, golden) == 1.0


def test_dedup_accuracy_keyword_overlap_match() -> None:
    """Fuzzy matching: keyword overlap (Jaccard > 0.5) matches."""
    golden = [{"candidate_title": "JWT token refresh strategy", "expected_action": "add"}]
    preds = [{"candidate_title": "JWT refresh token handling", "action": "add"}]
    assert check_dedup_accuracy(preds, golden) == 1.0


def test_dedup_accuracy_no_fuzzy_match() -> None:
    """Fuzzy matching: completely different titles do not match."""
    golden = [{"candidate_title": "Database migration", "expected_action": "add"}]
    preds = [{"candidate_title": "Frontend styling", "action": "add"}]
    assert check_dedup_accuracy(preds, golden) == 0.0


# --- _fuzzy_title_match ---


def test_fuzzy_title_match_exact() -> None:
    """Exact match returns True."""
    assert _fuzzy_title_match("Use JWT", "Use JWT")


def test_fuzzy_title_match_substring() -> None:
    """Substring containment returns True."""
    assert _fuzzy_title_match("Use JWT", "Use JWT with HS256 for auth")


def test_fuzzy_title_match_keyword_overlap() -> None:
    """Keyword Jaccard > 0.5 returns True."""
    assert _fuzzy_title_match("JWT token refresh strategy", "JWT refresh token handling")


def test_fuzzy_title_match_no_match() -> None:
    """Completely different titles return False."""
    assert not _fuzzy_title_match("Database migration", "Frontend styling")


# --- compute_ndcg ---


def test_ndcg_perfect_ranking() -> None:
    """Perfect ranking produces NDCG of 1.0."""
    assert abs(compute_ndcg(["a", "b"], {"a", "b"}, k=5) - 1.0) < 1e-9


def test_ndcg_no_relevant() -> None:
    """No relevant items in results produces NDCG of 0.0."""
    assert compute_ndcg(["a", "b"], set(), k=5) == 0.0


def test_ndcg_empty_results() -> None:
    """Empty results with relevant set produces NDCG of 0.0."""
    assert compute_ndcg([], {"a"}, k=5) == 0.0


def test_ndcg_partial_ranking() -> None:
    """Partial match computes correct NDCG."""
    ranked = ["a", "b", "c"]
    relevant = {"a", "c"}
    dcg = 1.0 / math.log2(2) + 0.0 / math.log2(3) + 1.0 / math.log2(4)
    idcg = 1.0 / math.log2(2) + 1.0 / math.log2(3)
    expected = dcg / idcg
    assert abs(compute_ndcg(ranked, relevant, k=5) - expected) < 1e-9


def test_ndcg_respects_k() -> None:
    """Only first k results are considered."""
    ranked = ["x", "a", "b"]
    relevant = {"a", "b"}
    # k=1: only "x" considered, which is not relevant
    assert compute_ndcg(ranked, relevant, k=1) == 0.0


# --- check_archive_precision ---


def test_archive_precision_all_correct() -> None:
    """All archived items are in should_archive set."""
    assert check_archive_precision(["a", "b"], {"a", "b"}, {"c"}) == 1.0


def test_archive_precision_half_wrong() -> None:
    """Half of archived items are wrong (in should_keep)."""
    assert check_archive_precision(["a", "c"], {"a"}, {"c"}) == 0.5


def test_archive_precision_nothing_archived_nothing_expected() -> None:
    """Nothing archived when nothing should be archived is correct."""
    assert check_archive_precision([], set(), set()) == 1.0


def test_archive_precision_nothing_archived_should_have() -> None:
    """Nothing archived when items should have been archived scores 0."""
    assert check_archive_precision([], {"a"}, set()) == 0.0


def test_archive_precision_all_wrong() -> None:
    """All archived items are in should_keep."""
    assert check_archive_precision(["x", "y"], set(), {"x", "y"}) == 0.0


# --- _parse_agent_output ---


def test_parse_agent_output_direct_json() -> None:
    """Direct JSON string is parsed correctly."""
    assert _parse_agent_output("codex", '{"completeness": 0.8}') == {
        "completeness": 0.8,
    }


def test_parse_agent_output_claude_structured_output() -> None:
    """Claude --json-schema wrapper with structured_output dict is preferred over result."""
    wrapper = json.dumps(
        {
            "result": "Done! I evaluated the extraction output carefully.",
            "structured_output": {
                "completeness": 0.85,
                "faithfulness": 0.90,
                "clarity": 0.75,
                "reasoning": "Good extraction with clear evidence.",
            },
        }
    )
    parsed = _parse_agent_output("claude", wrapper)
    assert parsed["completeness"] == 0.85
    assert parsed["faithfulness"] == 0.90
    assert parsed["clarity"] == 0.75
    assert parsed["reasoning"] == "Good extraction with clear evidence."


def test_parse_agent_output_claude_wrapper() -> None:
    """Claude wrapper JSON (result field) is unwrapped and parsed."""
    wrapper = json.dumps({"result": '{"completeness": 0.9}'})
    assert _parse_agent_output("claude", wrapper) == {"completeness": 0.9}


def test_parse_agent_output_claude_structured_output_none_falls_back() -> None:
    """When structured_output is None, fall back to result field."""
    wrapper = json.dumps(
        {
            "result": '{"completeness": 0.8}',
            "structured_output": None,
        }
    )
    assert _parse_agent_output("claude", wrapper) == {"completeness": 0.8}


def test_parse_agent_output_claude_prose_raises() -> None:
    """Claude wrapper with prose-only result (no structured_output) raises."""
    wrapper = json.dumps(
        {
            "result": "The extraction quality is excellent overall.",
        }
    )
    import pytest

    with pytest.raises(RuntimeError, match="Could not parse JSON"):
        _parse_agent_output("claude", wrapper)


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


# --- check_extraction_assertions ---


def test_extraction_assertions_all_found() -> None:
	"""All must_extract items match -> recall 1.0."""
	output = [
		{"title": "Use JWT tokens", "body": "Auth uses JWT with HS256"},
		{"title": "Deploy to AWS", "body": "Production on ECS"},
	]
	assertions = {
		"must_extract": [
			{"title_contains": "jwt", "body_contains": "hs256"},
			{"title_contains": "deploy"},
		],
	}
	result = check_extraction_assertions(output, assertions)
	assert result["recall"] == 1.0
	assert result["score"] == 1.0


def test_extraction_assertions_partial() -> None:
	"""1 of 2 must_extract items match -> recall 0.5."""
	output = [{"title": "Use JWT tokens", "body": "token auth"}]
	assertions = {
		"must_extract": [
			{"title_contains": "jwt"},
			{"title_contains": "deploy"},
		],
	}
	result = check_extraction_assertions(output, assertions)
	assert result["recall"] == 0.5


def test_extraction_assertions_none_found() -> None:
	"""0 must_extract items match -> recall 0.0."""
	output = [{"title": "Unrelated item", "body": "nothing here"}]
	assertions = {
		"must_extract": [
			{"title_contains": "jwt"},
			{"title_contains": "deploy"},
		],
	}
	result = check_extraction_assertions(output, assertions)
	assert result["recall"] == 0.0
	assert result["score"] == 0.0


def test_extraction_assertions_violation() -> None:
	"""must_not_extract item found -> penalty > 0."""
	output = [
		{"title": "Use JWT tokens", "body": "auth"},
		{"title": "Database schema", "body": "tables"},
	]
	assertions = {
		"must_not_extract": [{"title_contains": "database"}],
	}
	result = check_extraction_assertions(output, assertions)
	assert result["precision_penalty"] > 0.0
	# penalty = 1 violation / 2 items = 0.5
	assert result["precision_penalty"] == 0.5


def test_extraction_assertions_bounds_ok() -> None:
	"""Within min/max -> bounds_ok True."""
	output = [
		{"title": "A", "body": "a"},
		{"title": "B", "body": "b"},
	]
	assertions = {"min_candidates": 1, "max_candidates": 5}
	result = check_extraction_assertions(output, assertions)
	assert result["bounds_ok"] is True


def test_extraction_assertions_bounds_exceeded() -> None:
	"""Exceeds max -> bounds_ok False, score reduced by 20%."""
	output = [
		{"title": "A", "body": "a"},
		{"title": "B", "body": "b"},
		{"title": "C", "body": "c"},
	]
	assertions = {
		"must_extract": [{"title_contains": "a"}],
		"max_candidates": 2,
	}
	result = check_extraction_assertions(output, assertions)
	assert result["bounds_ok"] is False
	# recall = 1/1 = 1.0, penalty = 0, but bounds penalty -> score = 1.0 * 0.8
	assert abs(result["score"] - 0.8) < 1e-9


def test_extraction_assertions_empty() -> None:
	"""No assertions -> score 1.0."""
	result = check_extraction_assertions([], {})
	assert result["score"] == 1.0
	assert result["recall"] == 1.0
	assert result["bounds_ok"] is True


# --- check_summarization_assertions ---


def test_summarization_assertions_all_pass() -> None:
	"""All fields, limits, content -> score 1.0."""
	output = {
		"title": "JWT Auth Setup",
		"description": "Setting up authentication",
		"user_intent": "implement auth",
		"session_narrative": "The user worked on auth",
		"coding_agent": "claude",
	}
	assertions = {
		"must_contain_fields": ["title", "description", "coding_agent"],
		"max_narrative_words": 50,
		"max_intent_words": 20,
		"title_contains": "jwt",
		"coding_agent": "claude",
	}
	result = check_summarization_assertions(output, assertions)
	assert result["fields_ok"] is True
	assert result["limits_ok"] is True
	assert result["content_score"] == 1.0
	assert result["score"] == 1.0


def test_summarization_assertions_missing_field() -> None:
	"""Missing required field -> fields_ok False."""
	output = {
		"title": "JWT Auth",
		"user_intent": "auth",
		"session_narrative": "worked on it",
	}
	assertions = {
		"must_contain_fields": ["title", "description"],
	}
	result = check_summarization_assertions(output, assertions)
	assert result["fields_ok"] is False
	# score should be reduced (multiplied by 0.5 instead of 1.0)
	assert result["score"] == 0.5


def test_summarization_assertions_word_limit_exceeded() -> None:
	"""Narrative too long -> limits_ok False."""
	output = {
		"title": "Test",
		"session_narrative": "word " * 100,
		"user_intent": "short",
	}
	assertions = {
		"max_narrative_words": 10,
	}
	result = check_summarization_assertions(output, assertions)
	assert result["limits_ok"] is False
	# score multiplied by 0.8 for limits violation
	assert abs(result["score"] - 0.8) < 1e-9


def test_summarization_assertions_content_mismatch() -> None:
	"""Title doesn't contain expected substring -> content_score 0."""
	output = {
		"title": "Database Migration",
		"session_narrative": "worked on db",
		"user_intent": "migrate",
	}
	assertions = {
		"title_contains": "jwt",
	}
	result = check_summarization_assertions(output, assertions)
	assert result["content_score"] == 0.0
	assert result["score"] == 0.0


def test_summarization_assertions_empty_output() -> None:
	"""Not a dict -> score 0.0."""
	result = check_summarization_assertions("not a dict", {})
	assert result["fields_ok"] is False
	assert result["limits_ok"] is False
	assert result["content_score"] == 0.0
	assert result["score"] == 0.0
