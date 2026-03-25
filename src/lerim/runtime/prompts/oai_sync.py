"""Sync prompt builder for the OpenAI Agents SDK LerimOAIAgent."""

from __future__ import annotations

import json
from pathlib import Path


def build_oai_sync_prompt(
	*,
	trace_file: Path,
	memory_root: Path,
	run_folder: Path,
	artifact_paths: dict[str, Path],
	metadata: dict[str, str],
) -> str:
	"""Build the sync prompt for the OAI agent.

	Unlike the PydanticAI sync prompt, this version delegates all filesystem
	operations to the Codex tool rather than using explore/read/write/glob/grep
	tools directly.  The agent orchestrates the flow using:
	  - codex: sandboxed filesystem reads and writes
	  - write_memory: structured memory creation (decisions + learnings)
	  - extract_pipeline: DSPy extraction of memory candidates from trace
	  - summarize_pipeline: DSPy summarization of the session trace
	"""
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

Steps (execute in order):

1. SCAN EXISTING MEMORIES:
   Use codex to scan the existing memory directories:
   - {memory_root}/decisions/
   - {memory_root}/learnings/
   Read the files and build a mental map of what memories already exist.
   Note each memory's title, tags, and confidence score.
   If directories are empty or missing, proceed with an empty map.

2. EXTRACT + SUMMARIZE (one turn, parallel):
   Call extract_pipeline() and summarize_pipeline() together in the SAME tool-call turn.
   Paths, metadata, and output locations are handled automatically.
   Optionally pass guidance about what memories already exist to help the extract
   pipeline avoid redundant candidates.

3. READ EXTRACT RESULTS:
   Use codex to read the extract artifact at {artifact_paths["extract"]}.
   This JSON file contains the list of memory candidates produced by the
   extraction pipeline.

4. DEDUPE CANDIDATES AGAINST EXISTING MEMORIES:
   For each extracted candidate, use codex to search the existing memories
   for duplicates.  Compare titles, bodies, and tags.  Apply this decision
   policy for each candidate:
   - no_op: an existing memory has the SAME primitive + title + body.
   - update: an existing memory matches on primitive and content overlaps
     significantly (>= 72% estimated token overlap).
   - add: no matching memory found (new memory).

5. WRITE MEMORIES (one turn, parallel):
   Call write_memory() for every candidate classified as add or update.
   Pass these fields:
     write_memory(primitive="decision"|"learning", title=..., body=...,
                   confidence=0.0-1.0, tags="tag1,tag2", kind=...)
   kind is required for learnings: insight, procedure, friction, pitfall, or preference.
   IMPORTANT: write_memory is the ONLY tool for creating memory files.
   Skip candidates classified as no_op.

6. WRITE REPORT:
   Use codex to write a JSON report to {artifact_paths["memory_actions"]}
   with this structure:
   {{
     "run_id": "{metadata.get("run_id", "")}",
     "actions": [
       {{"action": "add"|"update"|"no_op", "candidate_title": "...", "reason": "..."}}
     ],
     "counts": {{"add": N, "update": N, "no_op": N}},
     "written_memory_paths": ["<absolute path to each written memory file>"],
     "trace_path": "{trace_file}"
   }}
   IMPORTANT: ALL file paths in the report MUST be absolute paths.

The summary pipeline writes directly to {memory_root}/summaries/. Do NOT write summary files yourself.

Return one short plain-text completion line when finished."""
