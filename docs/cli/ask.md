# lerim ask

Ask a question using accumulated memory as context.

## Overview

One-shot query: ask Lerim a question and get an answer informed by memories extracted from your agent sessions.

!!! note
    This command requires a running Lerim server. Start it with `lerim up` (Docker) or `lerim serve` (direct).

## Syntax

```bash
lerim ask <question> [--limit N] [--project NAME] [--json]
```

## Parameters

<div class="param-field">
  <div class="param-header">
    <span class="param-name">question</span>
    <span class="param-type">string</span>
    <span class="param-badge required">required</span>
  </div>
  <p class="param-desc">Your question (use quotes if it contains spaces).</p>
</div>

<div class="param-field">
  <div class="param-header">
    <span class="param-name">--limit</span>
    <span class="param-type">integer</span>
    <span class="param-badge default">default: 12</span>
  </div>
  <p class="param-desc">Maximum number of memory items to include as context.</p>
</div>

<div class="param-field">
  <div class="param-header">
    <span class="param-name">--project</span>
    <span class="param-type">string</span>
  </div>
  <p class="param-desc">Scope to a specific project. <strong>Note:</strong> Not yet implemented.</p>
</div>

<div class="param-field">
  <div class="param-header">
    <span class="param-name">--json</span>
    <span class="param-type">boolean</span>
    <span class="param-badge default">default: false</span>
  </div>
  <p class="param-desc">Output structured JSON instead of human-readable text.</p>
</div>

## Examples

### Basic question

Ask about authentication patterns:

```bash
lerim ask 'What auth pattern do we use?'
```

**Output:**

```
Based on your project memories, you use bearer token authentication
for API requests. This pattern was chosen for its simplicity and
compatibility with standard HTTP clients.
```

### Limited context

Limit the number of memories used as context:

```bash
lerim ask "How is the database configured?" --limit 5
```

### JSON output

Get structured output for parsing:

```bash
lerim ask "What testing framework do we use?" --json
```

**Output:**

```json
{
  "question": "What testing framework do we use?",
  "answer": "Your project uses pytest as the primary testing framework...",
  "memories_used": 8,
  "confidence": 0.87
}
```

## How it works

1. Your question is sent to the running Lerim server via HTTP POST to `/api/ask`
2. Lerim performs memory retrieval to find relevant decisions and learnings
3. The top N memories (default 12) are used as context
4. An LLM generates an answer informed by these memories
5. The answer is returned with citation evidence

!!! tip
    For best results, ask specific questions about decisions, patterns, or procedures in your project.

## Exit codes

- **0**: Success — answer generated
- **1**: Error — server not running or authentication failed
- **2**: Usage error — invalid arguments

## Related commands

<div class="grid cards" markdown>

-   :material-format-list-bulleted: **lerim memory list**

    ---

    Browse stored memory files

    [:octicons-arrow-right-24: lerim memory](memory.md)

-   :material-chart-box: **lerim status**

    ---

    Check server status

    [:octicons-arrow-right-24: lerim status](status.md)

</div>

## Notes

- Ask uses memory retrieval evidence to ground its answers
- If provider auth fails (missing API key), the CLI returns exit code 1
- The `--project` flag is reserved for future project-scoped queries
