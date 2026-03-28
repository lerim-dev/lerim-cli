# Dedup Quality Judge

You are evaluating the quality of dedup (deduplication) decisions made during a memory sync run.

## Context

- **Original trace**: `{trace_path}` -- the coding session being synced
- **Memory root**: `{memory_root}` -- existing memory files (decisions/, learnings/)
- **Predicted actions**: see below
- **Golden assertions**: see below

## Instructions

Use your Read and search tools to investigate the files above. Do NOT load entire files into context -- read strategically:

1. **Read a few memory files** in `{memory_root}/decisions/` and `{memory_root}/learnings/` to understand the existing memory state.
2. **Compare predicted actions** against golden assertions to see where classifications diverge.
3. **Read the original trace** at `{trace_path}` to verify whether add/update/no_op decisions make sense given the session content.

## Predicted Actions

```json
{predictions}
```

## Golden Assertions

```json
{golden}
```

## Scoring (each 0.0 to 1.0)

- **completeness** (weight 0.25): Did dedup find all duplicate/overlapping candidates? Were all candidates in the golden set properly classified? 1.0 = no missed duplicates.
- **faithfulness** (weight 0.25): Are dedup decisions grounded in actual memory content? Are update decisions justified by real overlap between candidate and existing memory? 1.0 = all decisions evidence-based.
- **coherence** (weight 0.20): Is the reasoning behind dedup decisions clear and consistent? Do add/update/no_op classifications follow a coherent strategy? 1.0 = excellent reasoning.
- **precision** (weight 0.30): No false-positive duplicates? Items classified as no_op or update should genuinely overlap with existing memories. Penalize marking distinct candidates as duplicates. 1.0 = no incorrect dedup matches.

## Response Format

Return ONLY valid JSON (no markdown fences, no extra text):

{"completeness": 0.0, "faithfulness": 0.0, "coherence": 0.0, "precision": 0.0, "reasoning": "Brief explanation of scores."}
