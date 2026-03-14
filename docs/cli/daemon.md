# lerim daemon

Run a continuous loop with independent sync and maintain intervals.

## Overview

The daemon runs sync (hot path) and maintain (cold path) on independent schedules. Sync discovers and extracts new sessions frequently; maintain refines existing memories less often. Sessions are processed sequentially in chronological order (oldest first) so that later sessions can build on memories from earlier ones.

!!! note
    `lerim serve` already includes the daemon loop. Use `lerim daemon` standalone only if you want the background loop without the HTTP API and dashboard.

## Syntax

```bash
lerim daemon [--once] [--max-sessions N] [--poll-seconds N]
```

## Parameters

<div class="param-field">
  <div class="param-header">
    <span class="param-name">--once</span>
    <span class="param-type">boolean</span>
    <span class="param-badge default">default: off</span>
  </div>
  <p class="param-desc">Run one sync + maintain cycle and exit. Useful for cron jobs or CI.</p>
</div>

<div class="param-field">
  <div class="param-header">
    <span class="param-name">--max-sessions</span>
    <span class="param-type">integer</span>
    <span class="param-badge default">default: from config</span>
  </div>
  <p class="param-desc">Maximum sessions to extract per sync cycle. Overrides <code>sync_max_sessions</code> from config.</p>
</div>

<div class="param-field">
  <div class="param-header">
    <span class="param-name">--poll-seconds</span>
    <span class="param-type">integer</span>
    <span class="param-badge default">default: from config</span>
  </div>
  <p class="param-desc">Override both sync and maintain intervals uniformly (in seconds). Minimum value is 30 seconds. When not set, intervals come from config.</p>
</div>

## Default intervals

Configured in `~/.lerim/config.toml`:

| Setting | Default | Description |
|---------|---------|-------------|
| `sync_interval_minutes` | `10` | How often to run sync (hot path) |
| `maintain_interval_minutes` | `60` | How often to run maintain (cold path) |

When `--poll-seconds` is set, it overrides **both** intervals to the same value.

!!! info
    The minimum value for `--poll-seconds` is 30 seconds. Values below this are clamped to 30 to prevent excessive API/disk usage.

## Examples

### Run forever with defaults

```bash
lerim daemon
```

**Output:**

```
Daemon started (sync every 10m, maintain every 60m)

[14:30:00] sync: 3 new sessions indexed, 2 memories extracted
[14:40:00] sync: 0 new sessions
[14:50:00] sync: 1 new session indexed, 1 memory extracted
[15:00:00] sync: 0 new sessions
[15:30:00] maintain: merged 2 duplicates, archived 1 low-value
```

### Single cycle

```bash
# Run once and exit — good for cron or testing
lerim daemon --once
```

**Output:**

```
Running single cycle...
  sync: 2 new sessions indexed, 1 memory extracted
  maintain: no changes
Done.
```

### Custom polling interval

```bash
# Override both intervals to 2 minutes
lerim daemon --poll-seconds 120
```

## Processing order

Sessions are processed **sequentially in chronological order** (oldest first). This ordering matters because:

1. An earlier session might introduce a new concept
2. A later session might refine or contradict that concept
3. Processing in order ensures the memory store reflects the latest understanding

!!! tip
    If you need to reprocess sessions, use `lerim sync --force` to re-extract already-processed sessions in the correct order.

## Exit codes

| Code | Meaning |
|------|---------|
| `0` | Clean shutdown (SIGINT/SIGTERM or `--once` completed) |
| `1` | Runtime failure |

## Related commands

<div class="grid cards" markdown>

-   :material-sync: **lerim sync**

    ---

    Run sync once (hot path)

    [:octicons-arrow-right-24: lerim sync](sync.md)

-   :material-wrench: **lerim maintain**

    ---

    Run maintain once (cold path)

    [:octicons-arrow-right-24: lerim maintain](maintain.md)

-   :material-server: **lerim serve**

    ---

    Full server with API, dashboard, and daemon

    [:octicons-arrow-right-24: lerim serve](serve.md)

-   :material-chart-box: **lerim status**

    ---

    Check daemon timing and queue depth

    [:octicons-arrow-right-24: lerim status](status.md)

</div>
