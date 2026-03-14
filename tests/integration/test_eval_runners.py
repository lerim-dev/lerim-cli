"""Layer 3: End-to-end eval runner integration tests.

Tests the full extract->judge->score and summarize->judge->score flows.
Also tests error recovery paths (missing traces, empty traces).
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

_skip_no_judge = pytest.mark.skipif(
    not os.environ.get("LERIM_JUDGE"),
    reason="LERIM_JUDGE not set",
)

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "traces"


def _write_eval_config(tmp_dir: Path) -> Path:
    """Write a minimal eval TOML config using the active test provider."""
    from lerim.config.settings import get_config

    cfg = get_config()
    provider = cfg.extract_role.provider
    model = cfg.extract_role.model
    thinking = "true" if cfg.extract_role.thinking else "false"
    config_path = tmp_dir / "eval_test.toml"
    config_path.write_text(
        f"""\
[judge]
agent = "claude"
timeout_seconds = 120

[lead]
provider = "{provider}"
model = "{model}"
thinking = {thinking}

[explorer]
provider = "{provider}"
model = "{model}"
thinking = {thinking}

[extraction]
provider = "{provider}"
model = "{model}"
thinking = {thinking}
max_window_tokens = 150000

[summarization]
provider = "{provider}"
model = "{model}"
thinking = {thinking}
max_window_tokens = 150000
""",
        encoding="utf-8",
    )
    return config_path


# ---------------------------------------------------------------------------
# Extraction eval runner tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
@_skip_no_judge
def test_extraction_eval_runner_e2e(tmp_path: Path) -> None:
    """Full extraction eval: pipeline + judge + scoring on fixture traces."""
    from evals.run_extraction import run_extraction_eval

    config_path = _write_eval_config(tmp_path)

    result = run_extraction_eval(config_path, traces_dir=FIXTURES_DIR, limit=1)

    assert isinstance(result, dict)
    assert result["pipeline"] == "extraction"
    assert "scores" in result
    assert "per_trace" in result
    assert len(result["per_trace"]) == 1

    scores = result["scores"]
    assert "composite" in scores
    assert "completeness" in scores
    assert "faithfulness" in scores
    assert "clarity" in scores

    # The pipeline should have produced candidates (schema_ok > 0)
    per_trace_0 = result["per_trace"][0]
    assert (
        per_trace_0.get("has_candidates") or per_trace_0.get("candidate_count", 0) > 0
    ), "Extraction eval produced 0 candidates"


@pytest.mark.integration
@_skip_no_judge
def test_summarization_eval_runner_e2e(tmp_path: Path) -> None:
    """Full summarization eval: pipeline + judge + scoring on fixture traces."""
    from evals.run_summarization import run_summarization_eval

    config_path = _write_eval_config(tmp_path)

    result = run_summarization_eval(config_path, traces_dir=FIXTURES_DIR, limit=1)

    assert isinstance(result, dict)
    assert result["pipeline"] == "summarization"
    assert "scores" in result
    assert "per_trace" in result
    assert len(result["per_trace"]) == 1

    per_trace_0 = result["per_trace"][0]
    assert per_trace_0.get("fields_present", False), "Summary missing required fields"


# ---------------------------------------------------------------------------
# Pipeline-only tests (no judge needed)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_extraction_eval_pipeline_only(tmp_path: Path) -> None:
    """Extraction pipeline runs without judge errors when traces exist."""
    config_path = _write_eval_config(tmp_path)

    # Use configure_dspy_from_eval to set up config, then run pipeline directly
    import tomllib

    from evals.common import cleanup_eval, configure_dspy_from_eval

    with config_path.open("rb") as f:
        config = tomllib.load(f)

    eval_cfg, temp_dir = configure_dspy_from_eval(config, prefix="test_pipeline_")
    try:
        from lerim.memory.extract_pipeline import extract_memories_from_session_file

        result = extract_memories_from_session_file(
            FIXTURES_DIR / "claude_simple.jsonl"
        )
        assert isinstance(result, list)
        assert len(result) > 0, "Pipeline produced 0 candidates"
    finally:
        cleanup_eval(temp_dir)


@pytest.mark.integration
def test_summarization_eval_pipeline_only(tmp_path: Path) -> None:
    """Summarization pipeline runs without judge errors when traces exist."""
    config_path = _write_eval_config(tmp_path)

    import tomllib

    from evals.common import cleanup_eval, configure_dspy_from_eval

    with config_path.open("rb") as f:
        config = tomllib.load(f)

    eval_cfg, temp_dir = configure_dspy_from_eval(config, prefix="test_pipeline_")
    try:
        from lerim.memory.summarization_pipeline import (
            summarize_trace_from_session_file,
        )

        result = summarize_trace_from_session_file(FIXTURES_DIR / "claude_simple.jsonl")
        assert isinstance(result, dict)
        assert result.get("title"), "Summary has no title"
        assert result.get("user_intent"), "Summary has no user_intent"
    finally:
        cleanup_eval(temp_dir)


# ---------------------------------------------------------------------------
# Error recovery tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_extraction_empty_trace_no_crash(tmp_path: Path) -> None:
    """Extraction pipeline on empty trace returns empty list, no crash."""
    config_path = _write_eval_config(tmp_path)

    import tomllib

    from evals.common import cleanup_eval, configure_dspy_from_eval

    with config_path.open("rb") as f:
        config = tomllib.load(f)

    eval_cfg, temp_dir = configure_dspy_from_eval(config, prefix="test_empty_")
    try:
        from lerim.memory.extract_pipeline import extract_memories_from_session_file

        empty_trace = tmp_path / "empty.jsonl"
        empty_trace.write_text("", encoding="utf-8")
        result = extract_memories_from_session_file(empty_trace)
        assert result == []
    finally:
        cleanup_eval(temp_dir)


@pytest.mark.integration
def test_summarization_empty_trace_raises(tmp_path: Path) -> None:
    """Summarization pipeline on empty trace raises RuntimeError."""
    config_path = _write_eval_config(tmp_path)

    import tomllib

    from evals.common import cleanup_eval, configure_dspy_from_eval

    with config_path.open("rb") as f:
        config = tomllib.load(f)

    eval_cfg, temp_dir = configure_dspy_from_eval(config, prefix="test_empty_")
    try:
        from lerim.memory.summarization_pipeline import (
            summarize_trace_from_session_file,
        )

        empty_trace = tmp_path / "empty.jsonl"
        empty_trace.write_text("", encoding="utf-8")
        with pytest.raises(RuntimeError, match="session_trace_empty"):
            summarize_trace_from_session_file(empty_trace)
    finally:
        cleanup_eval(temp_dir)


def test_extraction_missing_trace_raises(tmp_path: Path) -> None:
    """Extraction pipeline on missing trace raises FileNotFoundError."""
    from lerim.memory.extract_pipeline import extract_memories_from_session_file

    with pytest.raises(FileNotFoundError):
        extract_memories_from_session_file(tmp_path / "nonexistent.jsonl")


def test_summarization_missing_trace_raises(tmp_path: Path) -> None:
    """Summarization pipeline on missing trace raises FileNotFoundError."""
    from lerim.memory.summarization_pipeline import summarize_trace_from_session_file

    with pytest.raises(FileNotFoundError):
        summarize_trace_from_session_file(tmp_path / "nonexistent.jsonl")
