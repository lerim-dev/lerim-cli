"""Integration tests for sync memory-write contract behavior."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lerim.runtime.agent import LerimAgent


def _extract_artifacts_from_prompt(prompt: str) -> dict[str, str]:
    """Extract artifact path mapping from sync prompt payload."""
    for line in prompt.splitlines():
        if line.startswith("- artifact_paths_json: "):
            return json.loads(line.split(": ", 1)[1])
    raise AssertionError("artifact_paths_json not found in prompt")


def _extract_memory_root_from_prompt(prompt: str) -> Path:
    """Extract memory root path from sync prompt payload."""
    for line in prompt.splitlines():
        if line.startswith("- memory_root_path: "):
            return Path(line.split(": ", 1)[1].strip())
    raise AssertionError("memory_root_path not found in prompt")


def _fake_run_agent_once(
    _self: LerimAgent,
    *,
    prompt: str,
    mode: str,
    context,
):
    """Fake lead run writing complete artifact set for integration tests."""
    _ = context
    assert mode == "sync"
    artifacts = _extract_artifacts_from_prompt(prompt)
    memory_root = _extract_memory_root_from_prompt(prompt)

    Path(artifacts["extract"]).write_text("[]\n", encoding="utf-8")

    summary_memory_path = memory_root / "summaries" / "summary--s202602200001.md"
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
                "candidate_id": "0",
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
        "run_id": "run-1",
        "todos": [],
        "actions": [],
        "counts": {"add": 1, "update": 0, "no_op": 0},
        "written_memory_paths": [str(summary_memory_path)],
        "summary_path": str(summary_memory_path),
        "trace_path": "/tmp/trace.jsonl",
    }
    Path(artifacts["memory_actions"]).write_text(
        json.dumps(report, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return "ok", "session-1"


@pytest.mark.integration
def test_sync_writes_artifacts_and_summary_path(tmp_path: Path) -> None:
    """Sync contract writes all required artifacts and resolves summary_path."""
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text('{"role":"user","content":"hello"}\n', encoding="utf-8")

    agent = LerimAgent(default_cwd=str(tmp_path))
    agent._run_agent_once = _fake_run_agent_once.__get__(agent, LerimAgent)
    result = agent.sync(trace_path)

    assert Path(result["artifacts"]["extract"]).exists()
    assert Path(result["artifacts"]["summary"]).exists()
    assert Path(result["artifacts"]["memory_actions"]).exists()
    assert Path(result["artifacts"]["subagents_log"]).exists()
    assert "/summaries/" in result["summary_path"]
    assert Path(result["summary_path"]).exists()


@pytest.mark.integration
def test_sync_result_shape_keys_are_stable(tmp_path: Path) -> None:
    """Sync return payload keeps stable key contract for app compatibility."""
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text('{"role":"user","content":"hello"}\n', encoding="utf-8")

    agent = LerimAgent(default_cwd=str(tmp_path))
    agent._run_agent_once = _fake_run_agent_once.__get__(agent, LerimAgent)
    result = agent.sync(trace_path)

    assert set(result.keys()) == {
        "trace_path",
        "memory_root",
        "workspace_root",
        "run_folder",
        "artifacts",
        "counts",
        "written_memory_paths",
        "summary_path",
    }
