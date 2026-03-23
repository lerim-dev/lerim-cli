# Extraction Quality Judge

You are evaluating the quality of memory extraction from a coding-agent session trace.

## Instructions

1. Read the original trace file at: `{trace_path}`
2. Examine the extraction output below.
3. Score on four dimensions (each 0.0 to 1.0):
   - **completeness**: Did the extraction capture important decisions, preferences,
     hard-won insights, and friction from the trace? Note: it is FINE to skip generic
     research results, code-derivable facts, and ephemeral task details.
     1.0 = nothing genuinely important missed.
   - **faithfulness**: Are all items grounded in the trace? No hallucinated content?
     1.0 = perfectly faithful.
   - **clarity**: Are items clear, concise, with bodies that add information beyond
     the title? 1.0 = excellent clarity.
   - **precision**: Are ALL extracted items genuinely worth remembering for future
     sessions? Penalize heavily for:
     * Generic industry knowledge or research results from web searches
     * Code architecture facts that can be learned by reading the source code
     * Tautological items where the body merely restates the title
     * Ephemeral task details (slide edits, line-number fixes, TODO items)
     * Changelog-style entries (git log has these)
     1.0 = every single item is worth keeping. 0.5 = half are junk.
4. Return ONLY valid JSON (no markdown fences, no extra text).

## Extraction Output

```json
{output}
```

## Response Format

Return exactly this JSON structure (no markdown fences, no extra text):

{"completeness": 0.0, "faithfulness": 0.0, "clarity": 0.0, "precision": 0.0, "reasoning": "Brief explanation of scores."}
