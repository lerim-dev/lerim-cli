# Maintain Quality Judge

You are evaluating the quality of a memory maintenance run.

## Instructions

1. Examine the input memory files and the maintenance actions taken.
2. Score on three dimensions (each 0.0 to 1.0):
   - **completeness**: Did maintenance identify all opportunities for merging duplicates, archiving stale memories, and consolidating related items? 1.0 = no missed opportunities.
   - **faithfulness**: Are the maintenance actions reasonable and well-justified? No incorrect merges or inappropriate archives? 1.0 = all actions correct.
   - **coherence**: Is the overall maintenance report well-structured with clear reasoning for each action? 1.0 = excellent coherence.
3. Return ONLY valid JSON (no markdown fences, no extra text).

## Maintenance Report

```json
{output}
```

## Response Format

Return exactly this JSON structure:

```json
{
  "completeness": 0.0,
  "faithfulness": 0.0,
  "coherence": 0.0,
  "reasoning": "Brief explanation of scores."
}
```
