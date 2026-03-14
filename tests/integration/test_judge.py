"""Layer 2: Judge output parsing unit tests + end-to-end integration tests.

Unit-level tests validate _parse_agent_output with all known output formats
(structured_output, result fallback, prose, markdown code blocks).
Integration tests (gated by LERIM_JUDGE=1) invoke the real Claude CLI judge.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from evals.judge import (
    JUDGE_SCHEMA_CLARITY,
    JUDGE_SCHEMA_COHERENCE,
    _parse_agent_output,
    build_judge_prompt,
    invoke_judge,
)

pytestmark = pytest.mark.integration

_skip_no_judge = pytest.mark.skipif(
    not os.environ.get("LERIM_JUDGE"),
    reason="LERIM_JUDGE not set",
)

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "traces"


# ---------------------------------------------------------------------------
# Unit-level: _parse_agent_output (no external deps, always run)
# ---------------------------------------------------------------------------


class TestParseAgentOutput:
    """Tests for _parse_agent_output covering all known output formats."""

    def test_claude_structured_output(self) -> None:
        """Claude --json-schema returns structured_output dict — must be preferred."""
        wrapper = json.dumps(
            {
                "result": "Done! I evaluated the extraction output.",
                "structured_output": {
                    "completeness": 0.85,
                    "faithfulness": 0.90,
                    "clarity": 0.75,
                    "reasoning": "Good extraction coverage.",
                },
            }
        )
        parsed = _parse_agent_output("claude", wrapper)
        assert parsed["completeness"] == 0.85
        assert parsed["faithfulness"] == 0.90
        assert parsed["clarity"] == 0.75
        assert "reasoning" in parsed

    def test_claude_result_json_fallback(self) -> None:
        """Claude wrapper with JSON in result (no structured_output) is parsed."""
        wrapper = json.dumps(
            {
                "result": json.dumps(
                    {
                        "completeness": 0.7,
                        "faithfulness": 0.6,
                        "clarity": 0.5,
                        "reasoning": "Acceptable.",
                    }
                )
            }
        )
        parsed = _parse_agent_output("claude", wrapper)
        assert parsed["completeness"] == 0.7

    def test_claude_structured_output_empty_falls_back(self) -> None:
        """When structured_output is None/missing, fall back to result field."""
        wrapper = json.dumps(
            {
                "result": json.dumps(
                    {
                        "completeness": 0.8,
                        "faithfulness": 0.7,
                        "clarity": 0.6,
                        "reasoning": "ok",
                    }
                ),
                "structured_output": None,
            }
        )
        parsed = _parse_agent_output("claude", wrapper)
        assert parsed["completeness"] == 0.8

    def test_codex_direct_json(self) -> None:
        """Codex returns raw JSON dict."""
        raw = json.dumps(
            {
                "completeness": 0.9,
                "faithfulness": 0.8,
                "clarity": 0.7,
                "reasoning": "Great.",
            }
        )
        parsed = _parse_agent_output("codex", raw)
        assert parsed["completeness"] == 0.9

    def test_markdown_code_block_extraction(self) -> None:
        """JSON embedded in markdown code block is extracted."""
        md = (
            "Here is my evaluation:\n"
            "```json\n"
            '{"completeness": 0.6, "faithfulness": 0.5, "clarity": 0.4, "reasoning": "Mixed."}\n'
            "```\n"
            "That concludes my review."
        )
        parsed = _parse_agent_output("codex", md)
        assert parsed["completeness"] == 0.6

    def test_prose_only_raises(self) -> None:
        """Pure prose without any JSON raises RuntimeError."""
        with pytest.raises(RuntimeError, match="Could not parse JSON"):
            _parse_agent_output("codex", "This is just prose text with no JSON at all.")

    def test_claude_prose_in_result_raises(self) -> None:
        """Claude wrapper where result is prose (no structured_output) raises RuntimeError."""
        wrapper = json.dumps(
            {
                "result": "I evaluated the output carefully. The extraction is good.",
            }
        )
        with pytest.raises(RuntimeError, match="Could not parse JSON"):
            _parse_agent_output("claude", wrapper)


# ---------------------------------------------------------------------------
# Unit-level: build_judge_prompt
# ---------------------------------------------------------------------------


class TestBuildJudgePrompt:
    """Tests for judge prompt template rendering."""

    def test_placeholders_replaced(self, tmp_path: Path) -> None:
        """Template placeholders {trace_path} and {output} are replaced."""
        template = tmp_path / "tmpl.md"
        template.write_text(
            "Read the trace at {trace_path}\n\nPipeline output:\n{output}\n\nScore it.",
            encoding="utf-8",
        )
        prompt = build_judge_prompt(
            template, Path("/data/trace.jsonl"), '{"items": []}'
        )
        assert "/data/trace.jsonl" in prompt
        assert '{"items": []}' in prompt
        assert "{trace_path}" not in prompt
        assert "{output}" not in prompt


# ---------------------------------------------------------------------------
# Unit-level: schema constants
# ---------------------------------------------------------------------------


class TestSchemaConstants:
    """Tests for JUDGE_SCHEMA_CLARITY and JUDGE_SCHEMA_COHERENCE."""

    def test_clarity_schema_keys(self) -> None:
        """Clarity schema has completeness, faithfulness, clarity, reasoning."""
        assert set(JUDGE_SCHEMA_CLARITY["required"]) == {
            "completeness",
            "faithfulness",
            "clarity",
            "reasoning",
        }

    def test_coherence_schema_keys(self) -> None:
        """Coherence schema has completeness, faithfulness, coherence, reasoning."""
        assert set(JUDGE_SCHEMA_COHERENCE["required"]) == {
            "completeness",
            "faithfulness",
            "coherence",
            "reasoning",
        }


# ---------------------------------------------------------------------------
# Integration: invoke_judge (requires Claude CLI)
# ---------------------------------------------------------------------------


@_skip_no_judge
def test_judge_extraction_with_claude() -> None:
    """Invoke Claude CLI judge on extraction output, get valid scored JSON."""
    # Minimal extraction output
    extraction_output = json.dumps(
        [
            {
                "primitive": "decision",
                "title": "Use HS256 for JWT signing",
                "body": "Chose HS256 over RS256 for simplicity in single-service setup.",
                "confidence": 0.9,
                "tags": ["auth", "jwt"],
            }
        ],
        indent=2,
    )

    prompt = (
        "You are evaluating memory extraction quality. "
        "The trace is about JWT authentication setup. "
        f"Pipeline output:\n{extraction_output}\n\n"
        "Score completeness (0-1), faithfulness (0-1), clarity (0-1). "
        "Return JSON only."
    )

    result = invoke_judge("claude", prompt, timeout=120, schema=JUDGE_SCHEMA_CLARITY)
    assert isinstance(result, dict)
    assert "completeness" in result
    assert "faithfulness" in result
    assert "clarity" in result
    assert 0 <= result["completeness"] <= 1
    assert 0 <= result["faithfulness"] <= 1
    assert 0 <= result["clarity"] <= 1


@_skip_no_judge
def test_judge_summarization_with_claude() -> None:
    """Invoke Claude CLI judge on summarization output, get valid scored JSON."""
    summarization_output = json.dumps(
        {
            "title": "JWT Auth Setup Session",
            "description": "Set up JWT authentication with HS256 signing.",
            "user_intent": "Configure JWT-based authentication for the API.",
            "session_narrative": "User set up JWT auth, chose HS256, fixed CORS issues.",
            "coding_agent": "claude",
            "date": "2026-02-20",
            "time": "10:00:00",
            "tags": ["auth", "jwt", "cors"],
        },
        indent=2,
    )

    prompt = (
        "You are evaluating session summarization quality. "
        f"Pipeline output:\n{summarization_output}\n\n"
        "Score completeness (0-1), faithfulness (0-1), clarity (0-1). "
        "Return JSON only."
    )

    result = invoke_judge("claude", prompt, timeout=120, schema=JUDGE_SCHEMA_CLARITY)
    assert isinstance(result, dict)
    assert 0 <= result["completeness"] <= 1
    assert 0 <= result["faithfulness"] <= 1
    assert 0 <= result["clarity"] <= 1
