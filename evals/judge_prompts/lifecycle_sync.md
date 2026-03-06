# Lifecycle Sync Quality Judge

You are evaluating the quality of one sync run within a lifecycle eval. Memory accumulates across syncs — this is NOT a fresh empty memory directory.

## Context

- **Original trace**: `{trace_path}` — the coding session being synced
- **Agent trace**: `{agent_trace_path}` — full PydanticAI message history (tool calls, reasoning)
- **Memory root**: `{memory_root}` — existing memory files (decisions/ and learnings/)
- **Run folder**: `{run_folder}` — sync artifacts (extract.json, summary.json, memory_actions.json)
- **Memories before this sync**: {memory_count}

## Instructions

Use your Read and search tools to investigate the files above. Do NOT load entire files into context — read strategically:

1. **Read the trace** at `{trace_path}` — scan for key decisions, learnings, and friction points. Use offset/limit to sample representative sections.
2. **Read extract.json** in `{run_folder}` — check what candidates were extracted.
3. **Read memory_actions.json** in `{run_folder}` — check add/update/no_op decisions.
4. **If memories exist** (count > 0), read a few memory files in `{memory_root}/decisions/` and `{memory_root}/learnings/` to verify dedup/update decisions were reasonable.
5. **Read agent_trace.json** at `{agent_trace_path}` — sample the agent's reasoning to verify it investigated properly.

## Scoring (each 0.0 to 1.0)

- **completeness** (weight 0.4): Did the sync capture all important signals from the trace? Were valid extraction, summary, and memory_actions artifacts produced? If similar memories already existed, did it correctly choose update or no_op instead of duplicating?
- **faithfulness** (weight 0.35): Are extracted memories grounded in the trace? No hallucinated content? Are add/update/no_op decisions reasonable given existing memories?
- **coherence** (weight 0.25): Is the output well-organized? Do extraction, summary, and memory actions form a consistent picture? Are memory files well-written?

## Response Format

Return ONLY valid JSON (no markdown fences, no extra text):

```json
{
  "completeness": 0.0,
  "faithfulness": 0.0,
  "coherence": 0.0,
  "reasoning": "Brief explanation of scores."
}
```
