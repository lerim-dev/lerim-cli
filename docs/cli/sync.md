# lerim sync

Index new sessions and extract memories (hot path).

## Overview

Hot-path: discover new agent sessions from connected platforms, enqueue them, and run DSPy extraction to create memory primitives. Requires a running server (`lerim up` or `lerim serve`).

!!! note
    `sync` is the hot path (queue + DSPy extraction + lead decision/write). Cold maintenance work is handled by [`lerim maintain`](maintain.md).

## Syntax

```bash
lerim sync [options]
```

## Parameters

<div class="param-field">
  <div class="param-header">
    <span class="param-name">--window</span>
    <span class="param-type">string</span>
    <span class="param-badge default">default: "7d"</span>
  </div>
  <p class="param-desc">Relative time window: <code>30s</code>, <code>2m</code>, <code>1h</code>, <code>7d</code>, or <code>all</code>.</p>
</div>

<div class="param-field">
  <div class="param-header">
    <span class="param-name">--since</span>
    <span class="param-type">ISO-8601</span>
  </div>
  <p class="param-desc">Absolute start bound (overrides <code>--window</code>).</p>
</div>

<div class="param-field">
  <div class="param-header">
    <span class="param-name">--until</span>
    <span class="param-type">ISO-8601</span>
    <span class="param-badge default">default: now</span>
  </div>
  <p class="param-desc">Absolute end bound (only with <code>--since</code>).</p>
</div>

<div class="param-field">
  <div class="param-header">
    <span class="param-name">--agent</span>
    <span class="param-type">string</span>
    <span class="param-badge default">default: all</span>
  </div>
  <p class="param-desc">Comma-separated platform filter (e.g. <code>claude,codex</code>).</p>
</div>

<div class="param-field">
  <div class="param-header">
    <span class="param-name">--max-sessions</span>
    <span class="param-type">integer</span>
    <span class="param-badge default">default: 50</span>
  </div>
  <p class="param-desc">Max sessions to extract per run.</p>
</div>

<div class="param-field">
  <div class="param-header">
    <span class="param-name">--run-id</span>
    <span class="param-type">string</span>
  </div>
  <p class="param-desc">Target a single session by run ID (bypasses index scan).</p>
</div>

<div class="param-field">
  <div class="param-header">
    <span class="param-name">--no-extract</span>
    <span class="param-type">boolean</span>
    <span class="param-badge default">default: off</span>
  </div>
  <p class="param-desc">Index/enqueue only, skip extraction.</p>
</div>

<div class="param-field">
  <div class="param-header">
    <span class="param-name">--force</span>
    <span class="param-type">boolean</span>
    <span class="param-badge default">default: off</span>
  </div>
  <p class="param-desc">Re-extract already-processed sessions.</p>
</div>

<div class="param-field">
  <div class="param-header">
    <span class="param-name">--dry-run</span>
    <span class="param-type">boolean</span>
    <span class="param-badge default">default: off</span>
  </div>
  <p class="param-desc">Preview mode, no writes.</p>
</div>

<div class="param-field">
  <div class="param-header">
    <span class="param-name">--ignore-lock</span>
    <span class="param-type">boolean</span>
    <span class="param-badge default">default: off</span>
  </div>
  <p class="param-desc">Skip the writer lock check. Use with caution -- only when you know no other sync/maintain is running.</p>
</div>

## Examples

### Default sync

```bash
lerim sync                          # sync using configured window (default: 7d)
```

### Extended window

```bash
lerim sync --window 30d             # sync last 30 days
lerim sync --window all             # sync everything
```

### Filter by agent

```bash
lerim sync --agent claude,codex     # only sync these platforms
```

### Re-extract a specific session

```bash
lerim sync --run-id abc123 --force  # re-extract a specific session
```

### Absolute time bounds

```bash
lerim sync --since 2026-02-01T00:00:00Z --until 2026-02-08T00:00:00Z
```

### Index only (no extraction)

```bash
lerim sync --no-extract             # index and enqueue only, skip extraction
```

### Preview mode

```bash
lerim sync --dry-run                # preview what would happen, no writes
```

## Time window formats

Duration format: `<number><unit>` where unit is:

| Unit | Meaning |
|------|---------|
| `s` | Seconds |
| `m` | Minutes |
| `h` | Hours |
| `d` | Days |

Special value `all` scans all sessions ever recorded.

## Related commands

<div class="grid cards" markdown>

-   :material-wrench: **lerim maintain**

    ---

    Offline memory refinement

    [:octicons-arrow-right-24: lerim maintain](maintain.md)

-   :material-refresh: **lerim daemon**

    ---

    Continuous sync + maintain loop

    [:octicons-arrow-right-24: lerim daemon](daemon.md)

</div>
