# Golden Extraction Generator

You are producing a gold-standard memory extraction from a coding-agent session trace.
This will be used as ground truth for evaluating automated extraction pipelines.

## Instructions

1. Read the trace file at: `{trace_path}`
2. Extract ALL meaningful decisions, learnings, preferences, and friction signals.
3. For each item produce:
   - **primitive**: "decision" or "learning"
   - **kind**: one of "insight", "procedure", "friction", "pitfall", "preference" (for learnings)
   - **title**: short descriptive title
   - **body**: clear description in plain language
   - **confidence**: 0.0 to 1.0 (how confident you are this is a real signal)
   - **tags**: descriptive group labels
4. Be thorough but precise. Only include items grounded in the trace.
5. Return ONLY valid JSON (no markdown fences, no extra text).

## Response Format

Return exactly this JSON structure:

```json
{
  "candidates": [
    {
      "primitive": "decision",
      "kind": null,
      "title": "...",
      "body": "...",
      "confidence": 0.9,
      "tags": ["..."]
    }
  ]
}
```
