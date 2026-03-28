# Search Relevance Judge

You are evaluating the quality of memory search results returned by the hybrid FTS5 + vector search index.

## Context

- **Memory root**: `{memory_root}` -- indexed memory files (decisions/, learnings/)
- **Query**: `{query}`
- **Returned results** (ranked): see below
- **Known relevant memories**: see below

## Returned Results

```json
{results}
```

## Known Relevant Memories

```json
{relevant}
```

## Instructions

Use your Read and search tools to investigate the files above. Do NOT load entire files into context -- read strategically:

1. **Read the returned memory files** to verify they actually match the query intent.
2. **Read the known relevant memories** (by their file paths) to understand what should have been returned.
3. **Check ranking order**: Are the most relevant results ranked highest?

## Scoring (each 0.0 to 1.0)

- **completeness** (weight 0.25): Did the search find all known relevant memories within the top results? 1.0 = all relevant memories appeared in results.
- **faithfulness** (weight 0.25): Do the returned results actually match the query semantically? Are they genuinely about the topic being searched? 1.0 = all results are on-topic.
- **coherence** (weight 0.20): Is the ranking order reasonable? Are the most relevant results ranked first? 1.0 = perfect ranking.
- **precision** (weight 0.30): Are there irrelevant results in the top-5? Penalize results that do not relate to the query at all. 1.0 = no irrelevant results in top-5.

## Response Format

Return ONLY valid JSON (no markdown fences, no extra text):

{"completeness": 0.0, "faithfulness": 0.0, "coherence": 0.0, "precision": 0.0, "reasoning": "Brief explanation of scores."}
