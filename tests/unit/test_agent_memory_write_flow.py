"""Unit tests for the agent-led trace-path memory write flow.

These tests monkeypatch ``_run_agent_once`` so no real LLM call is made.
A dummy ``OPENROUTER_API_KEY`` is set via conftest autouse fixture.
"""

from __future__ import annotations

import json
from pathlib import Path

from lerim.runtime.agent import LerimAgent


def _extract_artifacts_from_prompt(prompt: str) -> dict[str, str]:
    """Extract artifact path mapping from the lead prompt."""
    for line in prompt.splitlines():
        if line.startswith("- artifact_paths_json: "):
            return json.loads(line.split(": ", 1)[1])
    raise AssertionError("artifact_paths_json not found in prompt")


def _extract_memory_root_from_prompt(prompt: str) -> Path:
    """Extract memory root path from the lead prompt."""
    for line in prompt.splitlines():
        if line.startswith("- memory_root_path: "):
            return Path(line.split(": ", 1)[1].strip())
    raise AssertionError("memory_root_path not found in prompt")


def _fake_agent_run_factory(
    candidates: list[dict[str, object]],
    *,
    run_counts: list[dict[str, int]] | None = None,
):
    """Build fake lead-runner that writes expected artifacts for each run call."""
    calls = {"index": 0}
    count_sequence = run_counts or [{"add": 1, "update": 0, "no_op": 0}]

    def _fake_run(
        _self: LerimAgent,
        *,
        prompt: str,
        mode: str,
        context,
    ):
        _ = context
        assert mode == "sync"
        artifacts = _extract_artifacts_from_prompt(prompt)
        memory_root = _extract_memory_root_from_prompt(prompt)
        idx = min(calls["index"], len(count_sequence) - 1)
        counts = count_sequence[idx]
        calls["index"] += 1

        Path(artifacts["extract"]).write_text(
            json.dumps(candidates, ensure_ascii=True, indent=2) + "\n", encoding="utf-8"
        )
        summary_memory_path = (
            memory_root / "summaries" / f"summary--s20260220{calls['index']:04d}.md"
        )
        summary_memory_path.parent.mkdir(parents=True, exist_ok=True)
        summary_memory_path.write_text(
            "---\nid: s202602200001\ntitle: Summary\n---\nSummary body\n",
            encoding="utf-8",
        )
        Path(artifacts["summary"]).write_text(
            json.dumps(
                {"summary_path": str(summary_memory_path)}, ensure_ascii=True, indent=2
            )
            + "\n",
            encoding="utf-8",
        )
        Path(artifacts["subagents_log"]).write_text(
            json.dumps(
                {
                    "candidate_id": 0,
                    "action_hint": "add",
                    "matched_file": "",
                    "evidence": "no strong match",
                },
                ensure_ascii=True,
            )
            + "\n",
            encoding="utf-8",
        )

        report = {
            "run_id": f"run-{calls['index']}",
            "todos": [],
            "actions": [],
            "counts": counts,
            "written_memory_paths": [str(summary_memory_path)],
            "summary_path": str(summary_memory_path),
            "trace_path": "/tmp/trace.jsonl",
        }
        Path(artifacts["memory_actions"]).write_text(
            json.dumps(report, ensure_ascii=True, indent=2) + "\n", encoding="utf-8"
        )
        return "ok", "session-1"

    return _fake_run


def test_agent_run_writes_summary_to_summaries_folder(monkeypatch, tmp_path) -> None:
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text('{"role":"user","content":"hello"}\n', encoding="utf-8")
    monkeypatch.setattr(
        LerimAgent,
        "_run_agent_once",
        _fake_agent_run_factory(
            [
                {
                    "primitive": "learning",
                    "title": "Queue retries",
                    "body": "Use bounded retries and heartbeat checks.",
                    "confidence": 0.9,
                }
            ]
        ),
    )

    agent = LerimAgent(default_cwd=str(tmp_path))
    result = agent.sync(trace_path)

    assert result["counts"]["add"] == 1
    assert result["counts"]["update"] == 0
    assert result["counts"]["no_op"] == 0
    assert "/summaries/" in result["summary_path"]
    assert Path(result["artifacts"]["extract"]).exists()
    assert Path(result["artifacts"]["summary"]).exists()
    assert Path(result["artifacts"]["memory_actions"]).exists()


def test_agent_run_marks_duplicate_candidate_as_no_op(monkeypatch, tmp_path) -> None:
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text('{"role":"user","content":"hello"}\n', encoding="utf-8")
    monkeypatch.setattr(
        LerimAgent,
        "_run_agent_once",
        _fake_agent_run_factory(
            [
                {
                    "primitive": "learning",
                    "title": "Queue retries",
                    "body": "Use bounded retries and heartbeat checks.",
                    "confidence": 0.9,
                }
            ],
            run_counts=[
                {"add": 1, "update": 0, "no_op": 0},
                {"add": 0, "update": 0, "no_op": 1},
            ],
        ),
    )

    agent = LerimAgent(default_cwd=str(tmp_path))
    first = agent.sync(trace_path)
    second = agent.sync(trace_path)

    assert first["counts"]["add"] == 1
    assert second["counts"]["no_op"] == 1
