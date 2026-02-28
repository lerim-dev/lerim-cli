"""Eval scoring dataclass and deterministic validation checks.

Provides EvalScore for recording per-trace eval results, composite score
computation, and schema/field validation for extraction and summarization outputs.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from lerim.memory.schemas import MemoryCandidate


@dataclass
class EvalScore:
    """Per-trace eval result with deterministic checks and judge scores."""

    trace: str
    schema_ok: bool
    has_candidates: bool = False
    fields_present: bool = False
    word_limits: bool = False
    completeness: float = 0.0
    faithfulness: float = 0.0
    clarity: float = 0.0
    composite: float = 0.0
    wall_time_s: float = 0.0
    judge_reasoning: str = ""
    candidate_count: int = 0


def compute_composite(completeness: float, faithfulness: float, clarity: float) -> float:
    """Weighted average: completeness 40%, faithfulness 35%, clarity 25%."""
    return completeness * 0.4 + faithfulness * 0.35 + clarity * 0.25


def check_extraction_schema(output: list[dict]) -> bool:
    """Validate each item in extraction output against MemoryCandidate schema."""
    if not isinstance(output, list) or not output:
        return False
    for item in output:
        try:
            MemoryCandidate.model_validate(item)
        except Exception:
            return False
    return True


def check_summarization_fields(output: dict) -> bool:
    """Check that required frontmatter fields are present in summarization output."""
    required = {"title", "description", "user_intent", "session_narrative", "coding_agent"}
    return isinstance(output, dict) and required.issubset(output.keys())


def check_word_limits(output: dict) -> bool:
    """Check user_intent <= 150 words, session_narrative <= 200 words."""
    if not isinstance(output, dict):
        return False
    intent_words = len(str(output.get("user_intent", "")).split())
    narrative_words = len(str(output.get("session_narrative", "")).split())
    return intent_words <= 150 and narrative_words <= 200


if __name__ == "__main__":
    """Self-test for scoring utilities."""
    assert compute_composite(1.0, 1.0, 1.0) == 1.0
    assert compute_composite(0.0, 0.0, 0.0) == 0.0
    assert abs(compute_composite(0.8, 0.6, 0.4) - (0.32 + 0.21 + 0.1)) < 1e-9

    assert check_extraction_schema([{"primitive": "decision", "title": "T", "body": "B"}])
    assert not check_extraction_schema([{"bad": "data"}])
    assert not check_extraction_schema([])

    assert check_summarization_fields({
        "title": "t", "description": "d", "user_intent": "u",
        "session_narrative": "s", "coding_agent": "c",
    })
    assert not check_summarization_fields({"title": "t"})

    assert check_word_limits({"user_intent": "a " * 150, "session_narrative": "b " * 200})
    assert not check_word_limits({"user_intent": "a " * 151, "session_narrative": "b"})

    score = EvalScore(trace="test.jsonl", schema_ok=True, has_candidates=True)
    assert score.trace == "test.jsonl"
    print("scores: self-test passed")
