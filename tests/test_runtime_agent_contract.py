"""Runtime agent contract tests for the PydanticAI migration.

A dummy ``OPENROUTER_API_KEY`` is set via conftest autouse fixture.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lerim.runtime.agent import LerimAgent
from lerim.runtime.prompts.system import build_lead_system_prompt


def test_system_prompt_enforces_lead_contract() -> None:
    prompt = build_lead_system_prompt()
    assert "lead runtime orchestrator" in prompt
    assert "read-only explorer subagent" in prompt


def test_default_read_only_tools_exist() -> None:
    agent = LerimAgent()
    assert "read" in agent.single_tools
    assert "explore" in agent.single_tools


def test_chat_mode_honors_single_tools_allowlist() -> None:
    agent = LerimAgent(single_tools=["read", "glob"])
    built = agent._build_lead_agent("chat")
    tool_names = set(built._function_toolset.tools.keys())
    assert tool_names == {"read", "glob"}


def test_sync_mode_uses_memory_write_toolset() -> None:
    agent = LerimAgent(single_tools=[])
    built = agent._build_lead_agent("sync")
    tool_names = set(built._function_toolset.tools.keys())
    assert "write" in tool_names
    assert "extract_pipeline" in tool_names
    assert "summarize_pipeline" in tool_names


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
    """Fake lead run writing expected sync artifacts for contract tests."""
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
        "actions": [
            {
                "candidate_id": "0",
                "action": "add",
                "matched_file": "",
                "evidence": "no strong match",
            }
        ],
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


def test_sync_contract_creates_workspace_folder(tmp_path: Path) -> None:
    trace_path = tmp_path / "session.jsonl"
    trace_path.write_text('{"role":"user","content":"hello"}\n', encoding="utf-8")

    agent = LerimAgent(default_cwd=str(tmp_path))
    agent._run_agent_once = _fake_run_agent_once.__get__(agent, LerimAgent)
    result = agent.sync(trace_path)

    assert result["trace_path"] == str(trace_path.resolve())
    assert Path(result["run_folder"]).exists()
    assert Path(result["artifacts"]["extract"]).name == "extract.json"
    assert Path(result["summary_path"]).exists()


def test_sync_fails_fast_on_missing_trace(tmp_path: Path) -> None:
    agent = LerimAgent(default_cwd=str(tmp_path))
    with pytest.raises(FileNotFoundError):
        agent.sync(tmp_path / "missing.jsonl")


def test_sync_prompt_uses_trace_path_not_trace_content(tmp_path: Path) -> None:
    trace_path = tmp_path / "session.jsonl"
    trace_payload = '{"role":"user","content":"NEVER_INLINE_THIS_TRACE_CONTENT"}\n'
    trace_path.write_text(trace_payload, encoding="utf-8")
    run_folder = tmp_path / "workspace" / "sync-20260220-120000-aaaaaa"
    artifact_paths = {
        "extract": run_folder / "extract.json",
        "summary": run_folder / "summary.json",
        "memory_actions": run_folder / "memory_actions.json",
        "agent_log": run_folder / "agent.log",
        "subagents_log": run_folder / "subagents.log",
        "session_log": run_folder / "session.log",
    }

    from lerim.runtime.prompts import build_sync_prompt

    prompt = build_sync_prompt(
        trace_file=trace_path,
        memory_root=tmp_path / "memory",
        run_folder=run_folder,
        artifact_paths=artifact_paths,
        metadata={
            "run_id": "run-1",
            "trace_path": str(trace_path),
            "repo_name": "lerim",
        },
    )

    assert str(trace_path.resolve()) in prompt
    assert "NEVER_INLINE_THIS_TRACE_CONTENT" not in prompt
    assert (
        "extract_pipeline(trace_path, output_path, metadata, metrics, guidance)"
        in prompt
    )
    assert (
        "summarize_pipeline(trace_path, output_path, metadata, metrics, guidance)"
        in prompt
    )
