"""Sync (memory-write) prompt builder for the LerimAgent lead orchestrator."""

from __future__ import annotations

import json
import shlex
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from lerim.memory.memory_record import memory_write_schema_prompt


def build_sync_prompt(
    *,
    trace_file: Path,
    memory_root: Path,
    run_folder: Path,
    artifact_paths: dict[str, Path],
    metadata: dict[str, str],
) -> str:
    """Build lead-agent prompt for the memory write flow."""
    metadata_json = json.dumps(metadata, ensure_ascii=True)
    artifact_json = json.dumps(
        {key: str(path) for key, path in artifact_paths.items()}, ensure_ascii=True
    )
    extract_cmd = f"""\
python3 -m lerim.memory.extract_pipeline \
--trace-path {shlex.quote(str(trace_file))} \
--output {shlex.quote(str(artifact_paths["extract"]))} \
--metadata-json {shlex.quote(metadata_json)} \
--metrics-json '{{}}'"""
    summary_cmd = f"""\
python3 -m lerim.memory.summarization_pipeline \
--trace-path {shlex.quote(str(trace_file))} \
--output {shlex.quote(str(artifact_paths["summary"]))} \
--memory-root {shlex.quote(str(memory_root))} \
--metadata-json {shlex.quote(metadata_json)} \
--metrics-json '{{}}'"""
    schema_rules = memory_write_schema_prompt()
    return f"""\
Run the Lerim agent-led memory write flow.

Inputs:
- trace_path: {trace_file}
- memory_root_path: {memory_root}
- run_folder_path: {run_folder}
- artifact_paths_json: {artifact_json}

Checklist:
- validate_inputs
- PARALLEL: call extract_pipeline AND summarize_pipeline together in the SAME tool-call turn (they are independent — both read the raw trace)
- explore for matching
- decide_add_update_no_op
- write memory files
- write run decision report

{schema_rules}

Execution rules:
- Do not inline or normalize trace content. Use only trace_path file access.
- Use runtime pipeline tools — call BOTH in the SAME response turn so they run in parallel:
  1) extract_pipeline(trace_path, output_path, metadata, metrics)
  2) summarize_pipeline(trace_path, output_path, metadata, metrics)
  (Equivalent reference commands: {extract_cmd} and {summary_cmd})
- Read extract.json from artifact paths.
- The summary pipeline writes the summary directly to memory_root/summaries/ via --memory-root. Do NOT write summary files yourself.
- For candidate matching, use explore(query) to gather evidence.
- Explorer subagent is read-only and returns evidence envelopes.
- Lead agent is the only writer and final decider.
- Deterministic decision policy for non-summary candidates:
  - no_op when matched memory has exact same primitive + title + body.
  - update when primitive matches and token-overlap score >= 0.72.
  - add otherwise.
- Write markdown memory files with YAML frontmatter in memory_root/decisions, memory_root/learnings using write tool.
- If extract returns 0 candidates, write an empty JSONL file to subagents_log (explorer is skipped).
- Write explorer outputs to {artifact_paths["subagents_log"]} as JSONL.
- Write run report JSON to {artifact_paths["memory_actions"]} with keys: run_id, todos, actions, counts, written_memory_paths, trace_path.
- Include overlap score evidence in actions when action is update/no_op.
- counts keys must be: add, update, no_op.
- Every written/updated file path must be absolute.

Return one short plain-text completion line."""


if __name__ == "__main__":
    with TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        trace_file = root / "trace.jsonl"
        trace_file.write_text('{"role":"user","content":"hello"}\n', encoding="utf-8")
        run_folder = root / "workspace" / "sync-selftest"
        artifact_paths = {
            "extract": run_folder / "extract.json",
            "summary": run_folder / "summary.json",
            "memory_actions": run_folder / "memory_actions.json",
            "agent_log": run_folder / "agent.log",
            "subagents_log": run_folder / "subagents.log",
            "session_log": run_folder / "session.log",
        }
        prompt = build_sync_prompt(
            trace_file=trace_file,
            memory_root=root / "memory",
            run_folder=run_folder,
            artifact_paths=artifact_paths,
            metadata={
                "run_id": "sync-selftest",
                "trace_path": str(trace_file),
                "repo_name": "lerim",
            },
        )
        assert "artifact_paths_json" in prompt
        assert "--memory-root" in prompt
        assert "Do NOT write summary files yourself" in prompt
