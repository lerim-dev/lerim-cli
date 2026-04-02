"""Extract agent: extract memories from a session trace, dedup, and write.

session trace -> dspy.ReAct(ExtractSignature, tools) -> memory files + report.
The ReAct agent loop and its internal predictors are optimizable by
MIPROv2, BootstrapFewShot, BootstrapFinetune, etc.
"""

from __future__ import annotations

import dspy

from lerim.agents.context import RuntimeContext
from lerim.agents.tools import make_extract_tools


class ExtractSignature(dspy.Signature):
	"""Extract durable memories from a coding-agent session trace.

	You are the memory extraction agent. Read the session trace, identify what's
	worth remembering for future sessions, and write memory files directly.

	Steps:

	1. ORIENT:
	   Call scan_memory_manifest() to see existing memories.
	   Call read_file(file_path=trace_path) to read the session trace.
	   If the trace is large, read_file returns a truncated view -- use it to
	   identify the key topics, then grep or read specific sections if needed.

	2. ANALYZE:
	   From the trace, identify items worth remembering. Apply these criteria:

	   EXTRACT (high-value only):
	   - user: role, goals, preferences, working style (about the person)
	   - feedback: corrections ("don't do X") AND confirmations ("yes, exactly")
	     Body: rule/fact -> **Why:** -> **How to apply:**
	   - project: decisions, context, constraints NOT in code or git
	     Body: fact/decision -> **Why:** -> **How to apply:**
	   - reference: pointers to external systems (dashboards, Linear projects, etc.)

	   DO NOT EXTRACT:
	   - Code patterns, architecture, file paths -- derivable by reading the code
	   - Git history, recent changes -- git log is authoritative
	   - Debugging solutions -- the fix is in the code
	   - Anything in CLAUDE.md or README
	   - Ephemeral task details, in-progress work
	   - Generic programming knowledge everyone knows
	   - Implementation details visible in the codebase

	   An empty session (no memories written) is valid for pure implementation sessions.

	3. DEDUP:
	   Compare each potential memory against the manifest from step 1.
	   - Existing memory covers same topic -> skip (no_op)
	   - Related but adds NEW info -> read the existing file, then edit_memory()
	   - No match -> write_memory()
	   Default to skipping when uncertain -- duplicates are worse than gaps.

	4. WRITE:
	   For each new memory:
	   write_memory(type="user"|"feedback"|"project"|"reference",
	                name="Short title (max 10 words)",
	                description="One-line hook for retrieval (~150 chars)",
	                body="Content: rule/fact, then **Why:**, then **How to apply:**")

	5. INDEX:
	   Call update_memory_index() with a fresh index of all memories.

	6. SUMMARIZE:
	   Call write_summary() with:
	   - title: Short session title (max 10 words)
	   - description: One-line description of what the session achieved
	   - user_intent: The user's overall goal (at most 150 words)
	   - session_narrative: What happened chronologically (at most 200 words)
	   - tags: Comma-separated topic tags

	7. REPORT:
	   Call write_report() to memory_actions_path with:
	   {"run_id": "...", "actions": [...], "counts": {"add": N, "update": N, "no_op": N},
	    "written_memory_paths": [...], "trace_path": "..."}

	Return a short completion line.
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
	memory_actions_path: str = dspy.InputField(
		desc="Path for the actions JSON report"
	)
	memory_index_path: str = dspy.InputField(
		desc="Path to MEMORY.md index file"
	)
	run_id: str = dspy.InputField(
		desc="Unique run identifier"
	)
	completion_summary: str = dspy.OutputField(
		desc="Short plain-text completion summary"
	)


class ExtractAgent(dspy.Module):
	"""DSPy ReAct module for the extract flow. Independently optimizable."""

	def __init__(self, ctx: RuntimeContext):
		super().__init__()
		self.react = dspy.ReAct(
			ExtractSignature,
			tools=make_extract_tools(ctx),
			max_iters=ctx.config.lead_role.max_iters_sync,
		)

	def forward(
		self,
		trace_path: str,
		memory_root: str,
		run_folder: str,
		memory_actions_path: str,
		memory_index_path: str,
		run_id: str,
	) -> dspy.Prediction:
		return self.react(
			trace_path=trace_path,
			memory_root=memory_root,
			run_folder=run_folder,
			memory_actions_path=memory_actions_path,
			memory_index_path=memory_index_path,
			run_id=run_id,
		)
