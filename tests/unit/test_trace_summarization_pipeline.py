"""test trace summarization pipeline."""

from __future__ import annotations

from pathlib import Path

import pytest

from lerim.memory import summarization_pipeline as pipeline


def test_summarize_trace_from_session_file_returns_frontmatter_fields(
    tmp_path, monkeypatch
) -> None:
    run_id = "run-summary-1"
    session_path = tmp_path / "sessions" / f"{run_id}.jsonl"
    session_path.parent.mkdir(parents=True, exist_ok=True)
    session_path.write_text(
        '{"role":"user","content":"Fix queue retries and heartbeat drift."}\n'
        '{"role":"assistant","content":"Implemented bounded retries and dead-letter fallback."}\n',
        encoding="utf-8",
    )

    monkeypatch.setattr(
        pipeline,
        "_summarize_trace_with_rlm",
        lambda *_args, **_kwargs: {
            "title": "Queue stability summary",
            "description": "Session addressed duplicate queue claims.",
            "user_intent": "Fix duplicate queue claims and stabilize the heartbeat mechanism.",
            "session_narrative": "The run identified a race condition and applied atomic claim checks with retry policy updates.",
            "date": "2026-02-20",
            "time": "08:10:00",
            "coding_agent": "cursor",
            "raw_trace_path": str(session_path),
            "run_id": run_id,
            "repo_name": "lerim",
        },
    )
    summary = pipeline.summarize_trace_from_session_file(
        session_path,
        metadata={"run_id": run_id, "repo_name": "lerim"},
        metrics={},
    )

    assert summary["title"]
    assert summary["description"]
    assert summary["date"]
    assert summary["time"]
    assert summary["coding_agent"] == "cursor"
    assert summary["raw_trace_path"] == str(session_path)
    assert summary["user_intent"]
    assert len(summary["user_intent"].split()) <= 150
    assert summary["session_narrative"]
    assert len(summary["session_narrative"].split()) <= 200


def test_summarize_trace_from_session_file_raises_on_missing_file(tmp_path) -> None:
    missing_path = tmp_path / "missing.jsonl"
    with pytest.raises(FileNotFoundError):
        pipeline.summarize_trace_from_session_file(
            missing_path, metadata={}, metrics={}
        )


def test_summarization_pipeline_module_is_memory_boundary() -> None:
    source = Path(pipeline.__file__).read_text(encoding="utf-8")
    assert "dspy.RLM" in source
    assert "MemoryRepository" not in source
    assert "search_memory" not in source
    assert "LerimAgent" not in source
