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

	The agent orchestrates the flow using lightweight tools for simple
	file operations and batch dedup for efficient similarity search:
	  - extract_pipeline: DSPy extraction of memory candidates from trace
	  - summarize_pipeline: DSPy summarization of the session trace
	  - read_file / list_files: lightweight file reading
	  - batch_dedup_candidates: batch dedup for all candidates in one call
	  - write_memory: structured memory creation (decisions + learnings)
	  - write_report: write JSON report files
	"""
	artifact_json = json.dumps(
		{key: str(path) for key, path in artifact_paths.items()}, ensure_ascii=True
	)

	return f"""\
Run the Lerim memory-write flow for one session trace.

Inputs:
- trace_path: {trace_file}
- memory_root: {memory_root}
- run_folder: {run_folder}
- artifact_paths: {artifact_json}

Steps (execute in order):

1. EXTRACT + SUMMARIZE:
   Call extract_pipeline() and summarize_pipeline() in the SAME tool-call turn.
   After this step you MUST have: extract artifact written, summary written.
   If extract_pipeline returns 0 candidates, skip to step 4 (write report with empty actions).

2. BATCH DEDUP:
   Call read_file(file_path="{artifact_paths["extract"]}") to get extract results.
   Then call batch_dedup_candidates(candidates_json=<file content>).
   After this step you MUST have: each candidate enriched with top-3 similar existing memories.

3. CLASSIFY AND WRITE:
   For each candidate, classify using the batch dedup results:
   - top_similarity very high AND same insight -> "no_op"
   - related topic but candidate adds new info -> "update"
   - no relevant match -> "add"

   IMPORTANT: Err on the side of "update" over "add". Before classifying as "add",
   explicitly name the closest existing memory and explain why it is NOT a match.

   For "add": call write_memory() with all fields.
   For "update": call write_memory() with the SAME title as the existing memory,
   incorporating new information into the body.
   Skip "no_op" candidates.

   write_memory(primitive="decision"|"learning", title=..., body=...,
                 confidence=0.0-1.0, tags="tag1,tag2", kind=...)
   kind is REQUIRED for learnings: "insight", "procedure", "friction", "pitfall", or "preference".
   write_memory is the ONLY tool for creating memory files. Do NOT write .md files directly.

4. WRITE REPORT:
   Call write_report() to write JSON to {artifact_paths["memory_actions"]}:
   {{
     "run_id": "{metadata.get("run_id", "")}",
     "actions": [{{"action": "add"|"update"|"no_op", "candidate_title": "...", "reason": "..."}}],
     "counts": {{"add": N, "update": N, "no_op": N}},
     "written_memory_paths": ["<absolute path per written memory>"],
     "trace_path": "{trace_file}"
   }}
   ALL file paths MUST be absolute.

Do NOT write summary files yourself -- summarize_pipeline handles that.

Return one short plain-text completion line when finished."""
