# Isolated Maintain Quality Judge

You are evaluating the quality of an isolated memory maintenance run against a golden dataset with known expected outcomes.

## Context

- **Memory root**: `{memory_root}` -- memory files (decisions/, learnings/, archived/)
- **Run folder**: `{run_folder}` -- maintain artifacts (maintain_actions.json)
- **Memories before maintain**: {before_count}
- **Memories after maintain**: {after_count}
- **Golden assertions**: see below

## Golden Assertions

```json
{assertions}
```

## Instructions

Use your Read and search tools to investigate the files above. Do NOT load entire files into context -- read strategically:

1. **List memory files** in `{memory_root}/decisions/` and `{memory_root}/learnings/` to see the post-maintain state.
2. **Read maintain_actions.json** in `{run_folder}` -- check what actions were taken (merge, archive, consolidate, unchanged).
3. **Check archived/** directory at `{memory_root}/archived/` for newly archived files. Cross-reference with should_archive list.
4. **Sample memory files** to verify merge decisions preserved important information from both sources.
5. **Compare against assertions**: Were should_archive items archived? Were should_merge items merged? Were should_keep items left untouched?

## Scoring (each 0.0 to 1.0)

- **completeness** (weight 0.25): Did maintenance find all merge and archive opportunities listed in the golden assertions? Were all memory files reviewed? Were should_merge groups actually merged? 1.0 = no missed opportunities.
- **faithfulness** (weight 0.25): Are maintenance actions reasonable? Do merges preserve important information from both originals? Are archive decisions justified (not discarding valuable content)? 1.0 = all actions correct.
- **coherence** (weight 0.20): Is the final memory store well-organized after maintenance? Do merged memories read naturally? Is the maintain report well-structured with clear reasoning? 1.0 = excellent coherence.
- **precision** (weight 0.30): Did maintenance correctly avoid archiving should_keep items? Were no valuable memories incorrectly archived or merged away? Reward archiving genuinely low-quality memories. 1.0 = no incorrect maintenance actions.

## Response Format

Return ONLY valid JSON (no markdown fences, no extra text):

{"completeness": 0.0, "faithfulness": 0.0, "coherence": 0.0, "precision": 0.0, "reasoning": "Brief explanation of scores."}
