# Lifecycle Maintain Quality Judge

You are evaluating the quality of a memory maintenance run within a lifecycle eval. The memory directory has been populated by previous syncs.

## Context

- **Agent trace**: `{agent_trace_path}` — full PydanticAI message history (tool calls, reasoning)
- **Memory root**: `{memory_root}` — memory files (decisions/, learnings/, archived/)
- **Run folder**: `{run_folder}` — maintain artifacts (maintain_actions.json)
- **Memories before maintain**: {before_count}
- **Memories after maintain**: {after_count}

## Instructions

Use your Read and search tools to investigate the files above. Do NOT load entire files into context — read strategically:

1. **List memory files** in `{memory_root}/decisions/` and `{memory_root}/learnings/` to see the current state.
2. **Read maintain_actions.json** in `{run_folder}` — check what actions were taken (merge, archive, consolidate, unchanged).
3. **Sample a few memory files** to verify merge/archive decisions were reasonable. Check for obvious duplicates that should have been merged, or stale entries that should have been archived.
4. **Check archived/** directory for any newly archived files.
5. **Read agent_trace.json** at `{agent_trace_path}` — sample the agent's reasoning for its maintenance decisions.

## Scoring (each 0.0 to 1.0)

- **completeness** (weight 0.25): Did maintenance identify all opportunities for merging duplicates, archiving stale/trivial memories, and consolidating related items? Were all memory files reviewed? 1.0 = no missed opportunities.
- **faithfulness** (weight 0.25): Are maintenance actions reasonable and well-justified? No incorrect merges or inappropriate archives? Were merge results faithful to the originals? 1.0 = all actions correct.
- **coherence** (weight 0.20): Is the maintenance report well-structured with clear reasoning? Do the before/after counts make sense given the actions? 1.0 = excellent coherence.
- **precision** (weight 0.30): Did maintenance correctly identify and archive low-quality memories? Reward archiving: generic research results, code-derivable facts, tautological items, ephemeral task details. Penalize leaving obvious junk untouched. 1.0 = all low-quality memories were handled.

## Response Format

Return ONLY valid JSON (no markdown fences, no extra text):

{"completeness": 0.0, "faithfulness": 0.0, "coherence": 0.0, "precision": 0.0, "reasoning": "Brief explanation of scores."}
