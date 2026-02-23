"""Tests for path-first extract pipeline behavior."""

from __future__ import annotations

from pathlib import Path

import pytest

from lerim.memory import extract_pipeline as pipeline


def test_extract_memories_from_session_file_returns_candidates(tmp_path, monkeypatch) -> None:
    session_path = tmp_path / "session.jsonl"
    session_path.write_text('{"role":"assistant","content":"ok"}\n', encoding="utf-8")

    monkeypatch.setattr(
        pipeline,
        "_extract_candidates_with_rlm",
        lambda *_args, **_kwargs: [
            {
                "primitive": "learning",
                "title": "Queue retries",
                "body": "Use bounded retries with heartbeat and dead-letter routing.",
                "confidence": 0.9,
            },
            {
                "primitive": "decision",
                "title": "Trace-path only",
                "body": "Lead runtime should receive only trace_path, not full trace payload.",
                "confidence": 0.8,
            },
        ],
    )

    result = pipeline.extract_memories_from_session_file(session_path, metadata={"run_id": "run-1"}, metrics={})
    assert len(result) == 2
    assert result[0]["primitive"] == "learning"
    assert result[1]["primitive"] == "decision"


def test_extract_memories_from_session_file_raises_on_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        pipeline.extract_memories_from_session_file(tmp_path / "missing.jsonl", metadata={}, metrics={})
