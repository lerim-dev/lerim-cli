"""Sync agent: extract memories from a session trace, dedup, and write.

session trace -> dspy.ReAct(SyncSignature, tools) -> memory files + report.
The ReAct agent loop and its internal predictors are optimizable by
MIPROv2, BootstrapFewShot, BootstrapFinetune, etc.
"""

from __future__ import annotations

import dspy

from lerim.runtime.context import RuntimeContext
from lerim.runtime.tools import bind_sync_tools


class SyncSignature(dspy.Signature):
	"""Run the Lerim memory-write flow for one session trace.

	Steps (execute in order):

	1. EXTRACT + SUMMARIZE:
	   Call extract_pipeline() and summarize_pipeline() in the SAME tool-call turn.
	   After this step you MUST have: extract artifact written, summary written.
	   If extract_pipeline returns 0 candidates, skip to step 4 (write report with
	   empty actions).

	2. BATCH DEDUP:
	   Call read_file(file_path=extract_artifact_path) to get extract results.
	   Then call batch_dedup_candidates(candidates_json=<file content>).
	   After this step you MUST have: each candidate enriched with top-3 similar
	   existing memories.

	3. CLASSIFY AND WRITE:
	   For each candidate, classify using the batch dedup results:

	   Classification rules (use top_similarity score from batch_dedup_candidates):
	   - top_similarity is normalized 0.0-1.0 similarity. It prefers semantic similarity
	     and falls back to lexical overlap when vector similarity is unavailable.
	   - top_similarity >= 0.65 AND the existing memory covers the same core topic
	     -> "no_op"
	   - top_similarity 0.40-0.65 AND same topic but candidate adds genuinely NEW
	     information not present in the existing memory -> "update"
	   - top_similarity < 0.40 OR no relevant match at all -> "add"
	   - top_similarity == 0.0 (no existing memories) -> always "add"

	   IMPORTANT DEDUP RULES:
	   - Default to "no_op" when uncertain. Duplicate memories are worse than missing ones.
	   - Before classifying as "add", you MUST name the closest existing memory and
	     explain specifically what new information the candidate contributes that the
	     existing memory does NOT already contain.
	   - Before classifying as "update", verify the candidate contains at least ONE
	     concrete fact (a specific tool name, error message, workaround, or rationale)
	     that is completely ABSENT from the existing memory. Rephrasing the same insight
	     from a different angle is NOT new information -- classify as "no_op" instead.
	   - TOPIC SATURATION: If batch_dedup shows 2+ existing memories with
	     similarity > 0.40 on the same topic, the topic is already well-covered.
	     Default to "no_op" unless the candidate contains information that CONTRADICTS
	     or SIGNIFICANTLY extends ALL existing memories on that topic.

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
	   kind is REQUIRED for learnings: "insight", "procedure", "friction", "pitfall",
	   or "preference".
	   write_memory is the ONLY tool for creating memory files. Do NOT write .md files
	   directly.

	4. WRITE REPORT:
	   Call write_report() to write JSON to memory_actions_path:
	   {
	     "run_id": "<from run_id input>",
	     "actions": [{"action": "add"|"update"|"no_op", "candidate_title": "...",
	                   "reason": "..."}],
	     "counts": {"add": N, "update": N, "no_op": N},
	     "written_memory_paths": ["<absolute path per written memory>"],
	     "trace_path": "<from trace_path input>"
	   }
	   ALL file paths MUST be absolute.

	Do NOT write summary files yourself -- summarize_pipeline handles that.

	Return one short plain-text completion line when finished.
	"""

	trace_path: str = dspy.InputField(
		desc="Absolute path to the session trace file"
	)
	memory_root: str = dspy.InputField(
		desc="Absolute path to the memory root directory"
	)
	run_folder: str = dspy.InputField(
		desc="Absolute path to the run workspace folder"
	)
	extract_artifact_path: str = dspy.InputField(
		desc="Path where extract_pipeline writes results"
	)
	memory_actions_path: str = dspy.InputField(
		desc="Path where write_report should write the actions JSON"
	)
	run_id: str = dspy.InputField(
		desc="Unique run identifier for this sync"
	)
	completion_summary: str = dspy.OutputField(
		desc="Short plain-text completion summary"
	)


class SyncAgent(dspy.Module):
	"""DSPy ReAct module for the sync flow. Independently optimizable."""

	def __init__(self, ctx: RuntimeContext):
		super().__init__()
		self.react = dspy.ReAct(
			SyncSignature,
			tools=bind_sync_tools(ctx),
			max_iters=ctx.config.lead_role.max_iters_sync,
		)

	def forward(
		self,
		trace_path: str,
		memory_root: str,
		run_folder: str,
		extract_artifact_path: str,
		memory_actions_path: str,
		run_id: str,
	) -> dspy.Prediction:
		return self.react(
			trace_path=trace_path,
			memory_root=memory_root,
			run_folder=run_folder,
			extract_artifact_path=extract_artifact_path,
			memory_actions_path=memory_actions_path,
			run_id=run_id,
		)
