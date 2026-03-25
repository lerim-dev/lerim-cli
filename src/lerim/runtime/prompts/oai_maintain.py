"""Maintain (memory maintenance) prompt builder for the OpenAI Agents SDK agent.

Extends the base maintain prompt with cross-session analysis and hot-memory
curation. All filesystem operations are delegated to the Codex tool.
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
	- Delegates all filesystem operations to the Codex tool
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
You are running Lerim memory maintenance — an offline refinement pass over existing memories.
This mimics how human memory works: consolidate, strengthen important memories, forget noise.
All filesystem operations must go through the codex tool.

Inputs:
- memory_root: {memory_root}
- run_folder: {run_folder} (use this for intermediate files to manage your context)
- artifact_paths: {artifact_json}
- hot_memory_path: {hot_memory_path}

Checklist:
- scan_memories_and_summaries
- cross_session_analysis
- analyze_duplicates
- merge_similar
- archive_low_value
- decay_check
- consolidate_related
- curate_hot_memory
- write_report

Instructions:

1. SCAN MEMORIES + SUMMARIES:
   Use codex to read all memory files in {memory_root}/decisions/ and {memory_root}/learnings/.
   Parse frontmatter (id, title, confidence, tags, created, updated) and body content.
   Also read recent summaries from {memory_root}/summaries/ (last 30 days only — skip older ones).
   Build a mental map of the project's knowledge state.
   IMPORTANT: Process memories in chronological order (oldest "created" date first).
   Later memories may update or supersede earlier ones, so always resolve conflicts
   in favor of the newer memory.

2. CROSS-SESSION ANALYSIS:
   Using the summaries and memories gathered in step 1, perform four analyses:

   a) Signal Amplification:
      Identify topics that appear in 3+ session summaries but have no corresponding
      memories or only low-confidence ones (< 0.5). These are recurring signals worth
      capturing. For each signal, note which summaries mentioned it and suggest whether
      a new memory should be created or an existing one upgraded.

   b) Contradiction Detection:
      Find memories that conflict with each other. Check if a newer session summary
      reversed an earlier decision. For each contradiction:
      - If one is clearly newer and supersedes the other, archive the older one.
      - If the contradiction is genuinely unresolved, annotate both memories with a
        note about the conflict (via codex edit).
      - Never silently discard contradictions.

   c) Gap Detection:
      Identify areas with heavy session activity (mentioned in many summaries) but
      thin memory coverage (few or no memories on the topic). List each gap with
      the relevant summary references.

   d) Cross-Agent Patterns:
      Session summaries include a "coding_agent" field (e.g. claude, cursor, codex,
      opencode). Look for patterns across different agents:
      - Decisions made in one agent that should inform work in another
        (e.g. backend decision in Claude that frontend work in Cursor should know)
      - Same error pattern or friction appearing across multiple agents
      - Knowledge that exists in sessions from one agent but is missing from
        sessions with another agent working on the same codebase
      For each cross-agent insight, note the agents involved and the actionable
      knowledge that should flow between them.

   Record all findings for inclusion in the final report.

3. ANALYZE DUPLICATES:
   Identify memories that cover the same topic or have substantially overlapping content.
   Group them by similarity.

4. MERGE SIMILAR:
   For memories with overlapping content about the same topic:
   - Keep the most comprehensive version as the primary.
   - Use codex to merge unique details from the secondary into the primary.
   - Update the primary's "updated" timestamp to now.
   - Use codex to copy the secondary to {memory_root}/archived/{{folder}}/
     (where folder is "decisions" or "learnings"), then edit the original to mark
     as archived.

5. ARCHIVE LOW-VALUE:
   Archive memories that are:
   - Very low confidence (< 0.3)
   - Trivial or obvious (e.g., "installed package X", "ran command Y" with no insight)
   - Superseded by a more complete memory covering the same ground
   Use codex to copy to archived/ folder, then edit original to mark archived.

6. DECAY CHECK:
   Apply time-based decay using the access statistics below.
{access_section}

7. CONSOLIDATE RELATED:
   When you find 3+ small related memories about the same broader topic, combine
   them into one comprehensive memory using write_memory tool:
   write_memory(primitive="decision"|"learning", title=..., body=...,
                confidence=0.0-1.0, tags="tag1,tag2", kind=...)
   kind is required for learnings: insight, procedure, friction, pitfall, or preference.
   Archive the originals via codex.

8. CURATE HOT MEMORY:
   Write a curated hot-memory file at {hot_memory_path} using codex.
   This file serves as a fast-access summary of the most important project knowledge.

   Format:
   ```
   # Hot Memory
   *Auto-curated by Lerim maintain — do not edit manually*

   ## Active Decisions
   - [decision title]: [one-line summary] (confidence: X.X)
   ...

   ## Key Learnings
   - [learning title]: [one-line summary] (confidence: X.X)
   ...

   ## Recent Context
   - [topic from recent summaries]: [brief context]
   ...

   ## Watch Out
   - [contradictions, gaps, or low-confidence areas to monitor]
   ...

   ## Cross-Agent Insights
   - [patterns detected across different coding agents]
   ...
   ```

   Selection criteria (~2000 tokens max, 20-30 items total):
   - Prioritize by: recency > confidence > session corroboration > access frequency
   - Exclude contradicted or archived memories
   - Include recent session topics from summaries (last 30 days)
   - Each item should be a single concise line

9. WRITE REPORT:
   Use codex to write a JSON report to {artifact_paths["maintain_actions"]} with keys:
   - run_id: the run folder name ("{run_folder.name}")
   - actions: list of {{"action", "source_path", "target_path", "reason"}} dicts
   - counts: {{"merged", "archived", "consolidated", "decayed", "unchanged"}}
   - cross_session_analysis: {{
       "signals": [{{"topic": "...", "summary_count": N, "recommendation": "..."}}],
       "contradictions": [{{"memory_a": "...", "memory_b": "...", "resolution": "..."}}],
       "gaps": [{{"topic": "...", "summary_refs": ["..."], "coverage": "..."}}],
       "cross_agent": [{{"agents": ["claude", "cursor"], "topic": "...", "insight": "..."}}]
     }}
   - All file paths must be absolute.

Rules:
- Use codex for ALL file reads and writes (no explore/read/write/glob/grep tools).
- ONLY read/write files under {memory_root}/ and {run_folder}/.
  Exception: hot-memory.md is written to {hot_memory_path}.
- Use write_memory() for creating new memory files (consolidation).
  Python builds markdown automatically.
- Do NOT touch {memory_root}/summaries/ — summaries are read-only during maintain.
  Exception: you MAY read summaries, but never write or modify them.
- Do NOT delete files. Always archive (soft-delete via copy to archived/).
- Be conservative: when unsure whether to merge or archive, leave it unchanged.
- Quality over quantity: fewer good memories are better than many noisy ones.

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
		# No explore tool references
		assert "explore()" not in prompt
		assert "read(" not in prompt
		assert "write(" not in prompt
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
