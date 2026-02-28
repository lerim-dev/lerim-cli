# Golden Summarization Generator

You are producing a gold-standard session summary from a coding-agent session trace.
This will be used as ground truth for evaluating automated summarization pipelines.

## Instructions

1. Read the trace file at: `{trace_path}`
2. Produce a comprehensive summary with:
   - **title**: short descriptive title for the session
   - **description**: one-line description of what the session achieved
   - **user_intent**: the user's overall goal (at most 150 words)
   - **session_narrative**: what actually happened chronologically (at most 200 words)
   - **coding_agent**: which agent was used (claude code, codex, cursor, windsurf, etc.)
   - **tags**: descriptive group labels
3. Ground all claims in the trace. Do not invent details.
4. Return ONLY valid JSON (no markdown fences, no extra text).

## Response Format

Return exactly this JSON structure:

```json
{
  "title": "...",
  "description": "...",
  "user_intent": "...",
  "session_narrative": "...",
  "coding_agent": "...",
  "tags": ["..."]
}
```
