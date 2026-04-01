"""Maintain agent: review, merge, archive, and consolidate memories.

memory store -> dspy.ReAct(MaintainSignature, tools) -> optimized memory store.
The ReAct agent loop and its internal predictors are optimizable by
MIPROv2, BootstrapFewShot, BootstrapFinetune, etc.
"""

from __future__ import annotations

from typing import Any

import dspy

from lerim.runtime.context import RuntimeContext
from lerim.runtime.tools import bind_maintain_tools


# ---------------------------------------------------------------------------
# Input formatter for MaintainSignature.access_stats
# ---------------------------------------------------------------------------

def format_access_stats_section(
	access_stats: list[dict[str, Any]] | None,
	decay_days: int,
	decay_archive_threshold: float,
	decay_min_confidence_floor: float,
	decay_recent_access_grace_days: int,
) -> str:
	"""Format access statistics and decay policy for the MaintainSignature.access_stats field."""
	if not access_stats:
		return (
			"ACCESS DECAY: No access data available yet. Skip decay-based archiving for this run. "
			"Memories will start being tracked once users query them via chat."
		)

	lines = [
		f"- {s['memory_id']}: last_accessed={s['last_accessed']}, "
		f"access_count={s['access_count']}"
		for s in access_stats
	]
	return f"""\
ACCESS STATISTICS (from chat usage tracking):
{chr(10).join(lines)}

DECAY POLICY:
- Calculate effective_confidence = confidence * decay_factor
- decay_factor = max({decay_min_confidence_floor}, 1.0 - (days_since_last_accessed / {decay_days}))
- Memories with NO access record: use days since "created" date instead.
- Archive candidates: effective_confidence < {decay_archive_threshold}
- Grace period: memories accessed within the last {decay_recent_access_grace_days} days must NOT be archived regardless of confidence.
- Apply decay check AFTER the standard quality-based archiving step."""


class MaintainSignature(dspy.Signature):
	"""Run Lerim memory maintenance -- an offline refinement pass that consolidates,
	strengthens, and prunes existing memories.

	Tool reference:
	- memory_search(query, mode="scan"|"keyword"|"similar"|"clusters") -- unified search
	- list_files(directory, pattern) / read_file(file_path) -- read files in detail
	- archive_memory(file_path) -- soft-delete to archived/ subfolder
	- edit_memory(file_path, new_content) -- replace file content (must start with ---)
	- write_memory(primitive, title, body, ...) -- create new memories (consolidation)
	- write_hot_memory(content) -- write hot-memory.md
	- write_report(file_path, content) -- write final JSON report

	Checklist (complete every item):
	- scan_memories_and_summaries
	- cross_session_analysis
	- analyze_duplicates
	- merge_similar
	- archive_low_value
	- decay_check
	- consolidate_related
	- curate_hot_memory
	- write_report

	Steps:

	1. SCAN MEMORIES + SUMMARIES:
	   Call memory_search(query="", mode="scan") for a compact catalog of all active
	   memories. Call memory_search(query="*", mode="keyword") for recent summaries.
	   Use read_file() ONLY for memories you need to examine in detail.
	   After this step you MUST have: a mental map of all memories, ordered by creation
	   date.
	   IMPORTANT: Resolve conflicts in favor of the newer memory.

	2. CROSS-SESSION ANALYSIS:
	   Perform four analyses using summaries and memories from step 1:

	   a) Signal Amplification: Topics in 3+ summaries with no memory or
	      confidence < 0.5. Note which summaries mention it. Recommend: create new or
	      upgrade existing.

	   b) Contradiction Detection: Memories that conflict with each other or were
	      reversed by a newer summary. Archive the older if superseded. Annotate both
	      via edit_memory if genuinely unresolved. NEVER silently discard contradictions.

	   c) Gap Detection: Heavy session activity but thin memory coverage. List each gap
	      with summary references.

	   d) Cross-Agent Patterns: Session summaries include a "coding_agent" field (e.g.
	      claude, cursor, codex, opencode). Find decisions, errors, or knowledge that
	      should flow between agents working on the same codebase.

	   Record all findings for the final report.

	3. ANALYZE DUPLICATES:
	   Call memory_search(query="", mode="clusters") for tag-based clusters.
	   Call memory_search(title=..., body=..., mode="similar") for semantic duplicates.
	   After this step you MUST have: grouped candidates for merge, keep, or archive.

	4. MERGE SIMILAR:
	   For overlapping memories on the same topic:
	   1. Pick the most comprehensive as primary.
	   2. Call edit_memory() to merge unique details from secondaries into primary.
	   3. Update "updated" timestamp in frontmatter.
	   4. Call archive_memory() on each secondary.

	   You MUST identify at least the top-5 near-duplicate groups. Look for:
	   - Same topic with different wording
	   - Same insight from different sessions
	   - Same decision documented multiple times

	   For pairs: merge newer content into older file (canonical ID), archive newer.

	5. ARCHIVE LOW-VALUE:
	   Archive via archive_memory() any memory that is:
	   - Very low confidence (< 0.3)
	   - Trivial (e.g. "installed package X" with no insight)
	   - Superseded by a more complete memory
	   Also archive ALL files matching: "no-candidates-*", "memory-write-flow-*",
	   "memory-actions-report-*", "codex-tool-unavail*", "sync-run-*", "test-*",
	   or with body < 50 characters.
	   IMPORTANT: Do a second pass after the first to catch stragglers.

	6. DECAY CHECK:
	   Apply time-based decay using the access_stats input field.
	   The access_stats field includes the full access statistics section and decay
	   policy parameters (decay_days, decay_archive_threshold,
	   decay_min_confidence_floor, decay_recent_access_grace_days).
	   Follow the DECAY POLICY rules exactly as provided there.

	7. CONSOLIDATE RELATED:
	   When 3+ small memories cover the same broader topic, combine into one via:
	   write_memory(primitive="decision"|"learning", title=..., body=...,
	                confidence=0.0-1.0, tags="tag1,tag2", kind=...)
	   kind is REQUIRED for learnings: "insight", "procedure", "friction", "pitfall",
	   "preference".
	   Archive originals via archive_memory().

	8. CURATE HOT MEMORY:
	   Call write_hot_memory(content) with a ~2000-token fast-access summary.

	   Format:
	   ```
	   # Hot Memory
	   *Auto-curated by Lerim maintain -- do not edit manually*

	   ## Active Decisions
	   - [title]: [one-line summary] (confidence: X.X)

	   ## Key Learnings
	   - [title]: [one-line summary] (confidence: X.X)

	   ## Recent Context
	   - [topic]: [brief context from recent summaries]

	   ## Watch Out
	   - [contradictions, gaps, low-confidence areas]

	   ## Cross-Agent Insights
	   - [patterns across different coding agents]
	   ```

	   Rules: ONE entry per topic (dedup). Weight last 48h heavily. Max ~10 items
	   per section. NO meta-observations about the system. Prioritize by:
	   recency > confidence > corroboration > access frequency.

	9. WRITE REPORT:
	   Call write_report() to write JSON to maintain_actions_path:
	   {
	     "run_id": "<name of run_folder>",
	     "actions": [{"action": str, "source_path": str, "target_path": str,
	                   "reason": str}],
	     "counts": {"merged": N, "archived": N, "consolidated": N, "decayed": N,
	                 "unchanged": N},
	     "cross_session_analysis": {
	       "signals": [{"topic": str, "summary_count": N, "recommendation": str}],
	       "contradictions": [{"memory_a": str, "memory_b": str, "resolution": str}],
	       "gaps": [{"topic": str, "summary_refs": [str], "coverage": str}],
	       "cross_agent": [{"agents": [str], "topic": str, "insight": str}]
	     }
	   }
	   ALL file paths MUST be absolute.

	Constraints:
	- ONLY read/write under memory_root and run_folder.
	  Exception: hot-memory.md at hot_memory_path.
	- Summaries (memory_root/summaries/) are read-only. Do NOT write or modify them.
	- Do NOT delete files. ALWAYS use archive_memory() for soft-delete.
	- When unsure whether to merge or archive, leave unchanged.

	Return one short plain-text completion line.
	"""

	memory_root: str = dspy.InputField(
		desc="Absolute path to the memory root directory"
	)
	run_folder: str = dspy.InputField(
		desc="Absolute path to the run workspace folder"
	)
	maintain_actions_path: str = dspy.InputField(
		desc="Path where write_report writes the maintain actions JSON"
	)
	hot_memory_path: str = dspy.InputField(
		desc="Path to hot-memory.md"
	)
	access_stats: str = dspy.InputField(
		desc="Access statistics and decay policy (formatted text)"
	)
	completion_summary: str = dspy.OutputField(
		desc="Short plain-text completion summary"
	)


class MaintainAgent(dspy.Module):
	"""DSPy ReAct module for the maintain flow. Independently optimizable."""

	def __init__(self, ctx: RuntimeContext):
		super().__init__()
		self.react = dspy.ReAct(
			MaintainSignature,
			tools=bind_maintain_tools(ctx),
			max_iters=ctx.config.lead_role.max_iters_maintain,
		)

	def forward(
		self,
		memory_root: str,
		run_folder: str,
		maintain_actions_path: str,
		hot_memory_path: str,
		access_stats: str,
	) -> dspy.Prediction:
		return self.react(
			memory_root=memory_root,
			run_folder=run_folder,
			maintain_actions_path=maintain_actions_path,
			hot_memory_path=hot_memory_path,
			access_stats=access_stats,
		)
