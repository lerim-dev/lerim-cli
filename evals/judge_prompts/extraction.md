# Extraction Quality Judge

You are evaluating the quality of memory extraction from a coding-agent session trace.

## Instructions

1. Read the original trace file at: `{trace_path}`
2. Examine the extraction output below.
3. Score on three dimensions (each 0.0 to 1.0):
   - **completeness**: Did the extraction capture all important decisions, learnings, preferences, and friction signals from the trace? 1.0 = nothing important missed.
   - **faithfulness**: Are all extracted items grounded in the trace? No hallucinated or invented content? 1.0 = perfectly faithful.
   - **clarity**: Are the extracted items clear, concise, and well-written? Good titles, coherent bodies? 1.0 = excellent clarity.
4. Return ONLY valid JSON (no markdown fences, no extra text).

## Extraction Output

```json
{output}
```

## Response Format

Return exactly this JSON structure:

```json
{
  "completeness": 0.0,
  "faithfulness": 0.0,
  "clarity": 0.0,
  "reasoning": "Brief explanation of scores."
}
```
