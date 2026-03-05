# Sync Quality Judge

You are evaluating the quality of a full agentic sync run from a coding-agent session trace.

## Instructions

1. Read the original trace file at: `{trace_path}`
2. Examine the sync artifacts below (extraction, summary, memory actions).
3. Score on three dimensions (each 0.0 to 1.0):
   - **completeness**: Did the sync capture all important decisions and learnings into memory files? Did it produce valid extraction, summary, and memory_actions artifacts? 1.0 = nothing important missed, all artifacts present.
   - **faithfulness**: Are extracted memories and summary grounded in the trace? No hallucinated content? Are memory action decisions (add/update/no_op) reasonable? 1.0 = perfectly faithful.
   - **coherence**: Is the overall sync output well-organized? Do the extraction, summary, and memory actions form a consistent picture? 1.0 = excellent coherence.
4. Return ONLY valid JSON (no markdown fences, no extra text).

## Sync Artifacts

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
