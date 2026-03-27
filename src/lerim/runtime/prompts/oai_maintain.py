"""Maintain (memory maintenance) prompt builder for the OpenAI Agents SDK agent.

Extends the base maintain prompt with cross-session analysis and hot-memory
curation. All operations use lightweight tools (list_files, read_file,
archive_memory, edit_memory, etc.).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lerim.runtime.prompts.maintain import (
	_format_access_stats_section,
	build_maintain_artifact_paths,
)


def build_oai_maintain_artifact_paths(run_folder: Path) -> dict[str, Path]:
	"""Return canonical workspace artifact paths for an OAI maintain run.

	Same as the base maintain artifact paths (maintain_actions, agent_log,
	subagents_log). Hot-memory is written to memory_root.parent, not the
	run folder, so it is not included here.
	"""
	return build_maintain_artifact_paths(run_folder)


def build_oai_maintain_prompt(
	*,
	memory_root: Path,
	run_folder: Path,
	artifact_paths: dict[str, Path],
	access_stats: list[dict[str, Any]] | None = None,
	decay_days: int = 180,
	decay_archive_threshold: float = 0.2,
	decay_min_confidence_floor: float = 0.1,
	decay_recent_access_grace_days: int = 30,
) -> str:
	"""Build lead-agent prompt for the OAI memory maintenance flow.

	Compared to the PydanticAI maintain prompt, this version:
	- Uses lightweight tools for all filesystem operations
	- Adds cross-session analysis (signal amplification, contradiction
	  detection, gap detection)
	- Adds hot-memory curation step
	"""
	artifact_json = json.dumps(
		{key: str(path) for key, path in artifact_paths.items()}, ensure_ascii=True
	)
	access_section = _format_access_stats_section(
		access_stats,
		decay_days,
		decay_archive_threshold,
		decay_min_confidence_floor,
		decay_recent_access_grace_days,
	)

	hot_memory_path = memory_root.parent / "hot-memory.md"

	return f"""\
Run Lerim memory maintenance -- an offline refinement pass that consolidates,
strengthens, and prunes existing memories.

Inputs:
- memory_root: {memory_root}
- run_folder: {run_folder}
- artifact_paths: {artifact_json}
- hot_memory_path: {hot_memory_path}

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
   Call memory_search(query="", mode="scan") for a compact catalog of all active memories.
   Call memory_search(query="*", mode="keyword") for recent summaries.
   Use read_file() ONLY for memories you need to examine in detail.
   After this step you MUST have: a mental map of all memories, ordered by creation date.
   IMPORTANT: Resolve conflicts in favor of the newer memory.

2. CROSS-SESSION ANALYSIS:
   Perform four analyses using summaries and memories from step 1:

   a) Signal Amplification: Topics in 3+ summaries with no memory or confidence < 0.5.
      Note which summaries mention it. Recommend: create new or upgrade existing.

   b) Contradiction Detection: Memories that conflict with each other or were reversed
      by a newer summary. Archive the older if superseded. Annotate both via edit_memory
      if genuinely unresolved. NEVER silently discard contradictions.

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
   Apply time-based decay using access statistics.
{access_section}

7. CONSOLIDATE RELATED:
   When 3+ small memories cover the same broader topic, combine into one via:
   write_memory(primitive="decision"|"learning", title=..., body=...,
                confidence=0.0-1.0, tags="tag1,tag2", kind=...)
   kind is REQUIRED for learnings: "insight", "procedure", "friction", "pitfall", "preference".
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
   Call write_report() to write JSON to {artifact_paths["maintain_actions"]}:
   {{
     "run_id": "{run_folder.name}",
     "actions": [{{"action": str, "source_path": str, "target_path": str, "reason": str}}],
     "counts": {{"merged": N, "archived": N, "consolidated": N, "decayed": N, "unchanged": N}},
     "cross_session_analysis": {{
       "signals": [{{"topic": str, "summary_count": N, "recommendation": str}}],
       "contradictions": [{{"memory_a": str, "memory_b": str, "resolution": str}}],
       "gaps": [{{"topic": str, "summary_refs": [str], "coverage": str}}],
       "cross_agent": [{{"agents": [str], "topic": str, "insight": str}}]
     }}
   }}
   ALL file paths MUST be absolute.

Constraints:
- ONLY read/write under {memory_root}/ and {run_folder}/.
  Exception: hot-memory.md at {hot_memory_path}.
- Summaries ({memory_root}/summaries/) are read-only. Do NOT write or modify them.
- Do NOT delete files. ALWAYS use archive_memory() for soft-delete.
- When unsure whether to merge or archive, leave unchanged.

Return one short plain-text completion line."""


if __name__ == "__main__":
	from tempfile import TemporaryDirectory

	with TemporaryDirectory() as tmp_dir:
		root = Path(tmp_dir)
		memory_root = root / "memory"
		run_folder = root / "workspace" / "maintain-selftest"
		artifact_paths = build_oai_maintain_artifact_paths(run_folder)

		# Without access stats
		prompt = build_oai_maintain_prompt(
			memory_root=memory_root,
			run_folder=run_folder,
			artifact_paths=artifact_paths,
		)
		assert "memory maintenance" in prompt
		assert "scan_memories_and_summaries" in prompt
		assert "cross_session_analysis" in prompt
		assert "analyze_duplicates" in prompt
		assert "merge_similar" in prompt
		assert "archive_low_value" in prompt
		assert "decay_check" in prompt
		assert "consolidate_related" in prompt
		assert "curate_hot_memory" in prompt
		assert "write_report" in prompt
		assert "hot-memory.md" in prompt
		assert "Signal Amplification" in prompt
		assert "Contradiction Detection" in prompt
		assert "Gap Detection" in prompt
		assert "Cross-Agent Patterns" in prompt
		assert "coding_agent" in prompt
		assert "cross_agent" in prompt
		assert "codex" in prompt
		assert "No access data available" in prompt
		# No old PydanticAI tool references
		assert "explore()" not in prompt
		# No standalone search tools — all consolidated into memory_search
		assert "scan_memories()" not in prompt
		assert "search_memories(" not in prompt
		assert "find_similar_memories(" not in prompt
		assert "find_merge_candidates(" not in prompt
		# Unified memory_search referenced
		assert "memory_search(" in prompt
		assert 'mode="scan"' in prompt
		assert 'mode="keyword"' in prompt
		assert 'mode="similar"' in prompt
		assert 'mode="clusters"' in prompt
		# read_file() and write_report() are fine; bare read()/write() are not
		assert "read_file" in prompt
		assert "list_files" in prompt
		assert "write_report" in prompt
		# Hot memory path should be parent of memory_root
		expected_hot = str(memory_root.parent / "hot-memory.md")
		assert expected_hot in prompt

		# With access stats
		stats = [
			{
				"memory_id": "20260221-deploy-tips",
				"last_accessed": "2026-02-20T10:00:00Z",
				"access_count": 5,
			},
			{
				"memory_id": "20260101-old-thing",
				"last_accessed": "2025-08-01T10:00:00Z",
				"access_count": 1,
			},
		]
		prompt_with_stats = build_oai_maintain_prompt(
			memory_root=memory_root,
			run_folder=run_folder,
			artifact_paths=artifact_paths,
			access_stats=stats,
			decay_days=180,
			decay_archive_threshold=0.2,
			decay_min_confidence_floor=0.1,
			decay_recent_access_grace_days=30,
		)
		assert "20260221-deploy-tips" in prompt_with_stats
		assert "20260101-old-thing" in prompt_with_stats
		assert "DECAY POLICY" in prompt_with_stats
		assert "effective_confidence" in prompt_with_stats
		assert "cross_session_analysis" in prompt_with_stats

		# Artifact paths match base
		base_paths = build_maintain_artifact_paths(run_folder)
		oai_paths = build_oai_maintain_artifact_paths(run_folder)
		assert base_paths == oai_paths

		print("oai_maintain prompt: all self-tests passed")
