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

   Classification rules (use top_similarity score from batch_dedup_candidates):
   - top_similarity is normalized 0.0-1.0 similarity. It prefers semantic similarity and
     falls back to lexical overlap when vector similarity is unavailable.
   - top_similarity >= 0.65 AND the existing memory covers the same core topic → "no_op"
   - top_similarity 0.40-0.65 AND same topic but candidate adds genuinely NEW information
     not present in the existing memory → "update"
   - top_similarity < 0.40 OR no relevant match at all → "add"
   - top_similarity == 0.0 (no existing memories) → always "add"

   IMPORTANT DEDUP RULES:
   - Default to "no_op" when uncertain. Duplicate memories are worse than missing ones.
   - Before classifying as "add", you MUST name the closest existing memory and
     explain specifically what new information the candidate contributes that the
     existing memory does NOT already contain.
   - Before classifying as "update", verify the candidate contains at least ONE concrete
     fact (a specific tool name, error message, workaround, or rationale) that is
     completely ABSENT from the existing memory. Rephrasing the same insight from a
     different angle is NOT new information — classify as "no_op" instead.
   - TOPIC SATURATION: If batch_dedup shows 2+ existing memories with similarity > 0.40
     on the same topic, the topic is already well-covered. Default to "no_op" unless
     the candidate contains information that CONTRADICTS or SIGNIFICANTLY extends ALL
     existing memories on that topic.

   For "add": call write_memory() with all fields.
   For "update": call write_memory() with the SAME title as the existing memory,
   incorporating new information into the body.
   Skip "no_op" candidates.

   Preserve the extracted candidate metadata when writing:
   - source_speaker: "user" | "agent" | "both"
   - durability: "permanent" | "project" | "session"
   - outcome: "worked" | "failed" | "unknown" (optional)

   write_memory(primitive="decision"|"learning", title=..., body=...,
                 confidence=0.0-1.0, tags="tag1,tag2", kind=...,
                 source_speaker=..., durability=..., outcome=...)
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
