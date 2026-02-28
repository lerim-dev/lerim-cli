# Summarization Quality Judge

You are evaluating the quality of session trace summarization from a coding-agent session.

## Instructions

1. Read the original trace file at: `{trace_path}`
2. Examine the summarization output below.
3. Score on three dimensions (each 0.0 to 1.0):
   - **completeness**: Does the summary capture the user's intent and what actually happened? Key actions, problems, and outcomes included? 1.0 = nothing important missed.
   - **faithfulness**: Is the summary grounded in the trace? No hallucinated or invented details? 1.0 = perfectly faithful.
   - **clarity**: Is the summary well-written, concise, and easy to understand? Good title and description? 1.0 = excellent clarity.
4. Return ONLY valid JSON (no markdown fences, no extra text).

## Summarization Output

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
