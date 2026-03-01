"""Sync (memory-write) prompt builder for the LerimAgent lead orchestrator."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory


def build_sync_prompt(
    *,
    trace_file: Path,
    memory_root: Path,
    run_folder: Path,
    artifact_paths: dict[str, Path],
    metadata: dict[str, str],
) -> str:
    """Build lead-agent prompt for the memory write flow."""
    artifact_json = json.dumps(
        {key: str(path) for key, path in artifact_paths.items()}, ensure_ascii=True
    )
    return f"""\
Run the Lerim agent-led memory write flow.

Inputs:
- trace_path: {trace_file}
- memory_root_path: {memory_root}
- run_folder_path: {run_folder}
- artifact_paths_json: {artifact_json}

Steps (minimize tool turns — batch parallel calls aggressively):

1. EXTRACT + SUMMARIZE (one turn, parallel):
   Call extract_pipeline() and summarize_pipeline() together in the SAME tool-call turn.
   Paths, metadata, and output locations are handled automatically. Only pass optional guidance.

2. READ EXTRACT RESULTS (one turn):
   Read extract.json from artifact paths.

3. EXPLORE (one turn, parallel):
   Call up to 4 explore() calls in the SAME turn for candidate matching.
   Explorer subagent is read-only. If 0 candidates, skip to step 5.

4. WRITE MEMORIES (one turn, parallel):
   Call ALL write_memory() calls in the SAME turn for every add/update candidate.
   write_memory(primitive="decision"|"learning", title=..., body=..., confidence=0.0-1.0, tags=[...], kind=...)
   kind is required for learnings: insight, procedure, friction, pitfall, or preference.
   IMPORTANT: write_memory is the ONLY tool for creating memory files. write() will reject memory paths.
   Decision policy:
   - no_op: matched memory has exact same primitive + title + body.
   - update: primitive matches and token-overlap score >= 0.72.
   - add: otherwise.

5. WRITE REPORTS (one turn, parallel):
   Call write() for ALL of these in the SAME turn:
   - Explorer outputs to {artifact_paths["subagents_log"]} as JSONL (empty file if no candidates).
   - Run report JSON to {artifact_paths["memory_actions"]} with keys: run_id, todos, actions, counts, written_memory_paths, trace_path.
     counts keys: add, update, no_op. Include overlap score evidence in actions when update/no_op.
     Every file path must be absolute.
   write() is ONLY for non-memory files (JSON artifacts, reports, logs).

The summary pipeline writes directly to memory_root/summaries/. Do NOT write summary files yourself.

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
        assert "extract_pipeline" in prompt
        assert "Do NOT write summary files yourself" in prompt
