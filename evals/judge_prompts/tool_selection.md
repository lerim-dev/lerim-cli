# Tool Selection Quality Judge

You are evaluating whether the lerim agent selected the correct tools in the correct order during a sync or maintain run.

## Context

- **Agent trace**: `{agent_trace_path}` -- OpenAI Agents SDK run history with tool calls and results
- **Expected tool sequence**: see below
- **Forbidden tools**: see below
- **Actual tool calls**: see below

## Expected Sequence

```json
{expected_sequence}
```

## Forbidden Tools (must_not_call)

```json
{must_not_call}
```

## Actual Tool Calls

```json
{actual_calls}
```

## Instructions

Use your Read tool to examine the agent trace at `{agent_trace_path}` if needed for deeper context.

1. **Compare tool ordering**: Did the agent call tools in the expected order? Extract/summarize first, then dedup, then classify, then write.
2. **Check forbidden calls**: Were any must_not_call tools invoked? This is a hard penalty.
3. **Verify tool arguments**: Were the arguments passed to each tool reasonable for the task?
4. **Check for unnecessary calls**: Did the agent make redundant or wasted tool calls?

## Scoring (each 0.0 to 1.0)

- **completeness** (weight 0.25): Were all necessary tools called? Did the agent complete the full pipeline without skipping steps? 1.0 = all expected tools were called.
- **faithfulness** (weight 0.25): Were tool arguments correct and matched to the task? Did the agent pass appropriate data between tools? 1.0 = all arguments well-formed and task-appropriate.
- **coherence** (weight 0.20): Was the tool ordering logical? Did the agent follow the expected pipeline sequence? 1.0 = perfect ordering.
- **precision** (weight 0.30): Were there unnecessary tool calls or forbidden tool invocations? Penalize redundant calls and must_not_call violations heavily. 1.0 = no wasted or forbidden calls.

## Response Format

Return ONLY valid JSON (no markdown fences, no extra text):

{"completeness": 0.0, "faithfulness": 0.0, "coherence": 0.0, "precision": 0.0, "reasoning": "Brief explanation of scores."}
