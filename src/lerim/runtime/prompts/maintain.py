"""Maintain (memory maintenance) prompt builder for the LerimAgent."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from lerim.memory.memory_record import memory_write_schema_prompt


def build_maintain_artifact_paths(run_folder: Path) -> dict[str, Path]:
    """Return canonical workspace artifact paths for a maintain run folder."""
    return {
        "maintain_actions": run_folder / "maintain_actions.json",
        "agent_log": run_folder / "agent.log",
        "subagents_log": run_folder / "subagents.log",
    }


def _format_access_stats_section(
    access_stats: list[dict[str, Any]] | None,
    decay_days: int,
    decay_archive_threshold: float,
    decay_min_confidence_floor: float,
    decay_recent_access_grace_days: int,
) -> str:
    """Format access statistics and decay policy as prompt section."""
    if not access_stats:
        return """
ACCESS DECAY: No access data available yet. Skip decay-based archiving for this run.
Memories will start being tracked once users query them via chat."""

    lines = [
        f"- {s['memory_id']}: last_accessed={s['last_accessed']}, "
        f"access_count={s['access_count']}"
        for s in access_stats
    ]
    return f"""
ACCESS STATISTICS (from chat usage tracking):
{chr(10).join(lines)}

DECAY POLICY:
- Calculate effective_confidence = confidence * decay_factor
- decay_factor = max({decay_min_confidence_floor}, 1.0 - (days_since_last_accessed / {decay_days}))
- Memories with NO access record: use days since "created" date instead.
- Archive candidates: effective_confidence < {decay_archive_threshold}
- Grace period: memories accessed within the last {decay_recent_access_grace_days} days must NOT be archived regardless of confidence.
- Apply decay check AFTER the standard quality-based archiving step."""


def build_maintain_prompt(
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
    """Build lead-agent prompt for the memory maintenance flow."""
    artifact_json = json.dumps(
        {key: str(path) for key, path in artifact_paths.items()}, ensure_ascii=True
    )
    schema_rules = memory_write_schema_prompt()
    access_section = _format_access_stats_section(
        access_stats,
        decay_days,
        decay_archive_threshold,
        decay_min_confidence_floor,
        decay_recent_access_grace_days,
    )
    return f"""\
You are running Lerim memory maintenance — an offline refinement pass over existing memories.
This mimics how human memory works: consolidate, strengthen important memories, forget noise.

Inputs:
- memory_root: {memory_root}
- run_folder: {run_folder} (use this for intermediate files to manage your context)
- artifact_paths: {artifact_json}

Checklist:
- scan_memories
- analyze_duplicates
- merge_similar
- archive_low_value
- decay_check
- consolidate_related
- write_report

Instructions:

1. SCAN: Use explore, read, glob, grep tools to inspect all memory files in {memory_root}/decisions/ and {memory_root}/learnings/. Parse frontmatter (id, title, confidence, tags, created, updated) and body content.

2. ANALYZE DUPLICATES: Identify memories that cover the same topic or have substantially overlapping content. Group them by similarity.

3. MERGE: For memories with overlapping content about the same topic:
   - Keep the most comprehensive version as the primary.
   - Merge unique details from the secondary into the primary using edit.
   - Update the primary's "updated" timestamp to now.
   - Archive the secondary by writing it to {memory_root}/archived/{{folder}}/ (where folder is "decisions" or "learnings") using write, then edit the original to mark as archived.

4. ARCHIVE LOW-VALUE: Archive memories that are:
   - Very low confidence (< 0.3)
   - Trivial or obvious (e.g., "installed package X", "ran command Y" with no insight)
   - Superseded by a more complete memory covering the same ground
   Use write to copy to archived/ folder, then edit original to mark archived.

5. DECAY CHECK: Apply time-based decay using the access statistics below.
{access_section}

6. CONSOLIDATE: When you find 3+ small related memories about the same broader topic, consider combining them into one comprehensive memory file. Write the new consolidated memory via Write tool. Archive the originals.

7. REPORT: Write a JSON report to {artifact_paths["maintain_actions"]} with keys:
   - run_id: the run folder name
   - actions: list of {{"action", "source_path", "target_path", "reason"}} dicts
   - counts: {{"merged", "archived", "consolidated", "decayed", "unchanged"}}
   - All file paths must be absolute.

Rules:
- You are the only writer. Explore subagents are read-only.
- Memory files use YAML frontmatter between --- delimiters.
- {schema_rules}
- When writing new/updated memory files, follow the same frontmatter schema.
- Do NOT touch {memory_root}/summaries/ — summaries are managed by the pipeline only.
- Do NOT delete files. Always archive (soft-delete via mv to archived/).
- Be conservative: when unsure whether to merge or archive, leave it unchanged.
- Quality over quantity: fewer good memories are better than many noisy ones.

Return one short plain-text completion line."""


if __name__ == "__main__":
    with TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        run_folder = root / "workspace" / "maintain-selftest"
        artifact_paths = build_maintain_artifact_paths(run_folder)

        # Without access stats
        prompt = build_maintain_prompt(
            memory_root=root / "memory",
            run_folder=run_folder,
            artifact_paths=artifact_paths,
        )
        assert "memory maintenance" in prompt
        assert "scan_memories" in prompt
        assert "analyze_duplicates" in prompt
        assert "merge_similar" in prompt
        assert "archive_low_value" in prompt
        assert "decay_check" in prompt
        assert "consolidate_related" in prompt
        assert "write_report" in prompt
        assert "maintain_actions" in prompt
        assert "Do NOT touch" in prompt
        assert "soft-delete" in prompt
        assert "No access data available" in prompt

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
        prompt_with_stats = build_maintain_prompt(
            memory_root=root / "memory",
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
        assert "decayed" in prompt_with_stats
        print("maintain prompt: all self-tests passed")
