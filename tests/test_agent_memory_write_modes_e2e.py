"""E2E-style contract tests for sync memory-write mode."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

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


def _fake_agent_run_factory() -> Any:
    """Build fake lead runner that emits add, update, then no_op across runs."""
    sequence = [
        {"add": 1, "update": 0, "no_op": 0},
        {"add": 0, "update": 1, "no_op": 0},
        {"add": 0, "update": 0, "no_op": 1},
    ]
    state = {"index": 0}

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
        counts = sequence[min(state["index"], len(sequence) - 1)]
        state["index"] += 1

        Path(artifacts["extract"]).write_text("[]\n", encoding="utf-8")

        summary_memory_path = (
            memory_root / "summaries" / f"summary--s20260220{state['index']:04d}.md"
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
            "run_id": f"run-{state['index']}",
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


@pytest.mark.e2e
def test_sync_flow_contract(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """E2E contract: sync produces run folder + artifacts + correct result."""
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text('{"role":"user","content":"hello"}\n', encoding="utf-8")
    monkeypatch.setattr(LerimAgent, "_run_agent_once", _fake_agent_run_factory())

    agent = LerimAgent(default_cwd=str(tmp_path))
    sync = agent.sync(trace_path)

    assert Path(sync["run_folder"]).name.startswith("sync-")
    assert sync["counts"]["add"] == 1
    assert Path(sync["artifacts"]["extract"]).exists()
    assert Path(sync["artifacts"]["summary"]).exists()
    assert Path(sync["artifacts"]["memory_actions"]).exists()
    assert Path(sync["summary_path"]).exists()
    assert "/summaries/" in sync["summary_path"]
