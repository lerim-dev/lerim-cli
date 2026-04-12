# lerim status

Print runtime state: connected platforms, memory count, queue stats, and latest run timestamps.

## Overview

A quick health-check for your Lerim instance. Human-readable output shows key counts (connected agents, memory files, indexed sessions, queue summary). Use `--json` for full platform and latest-run metadata.

!!! note
    This command requires a running server. Start it with `lerim up` (Docker) or `lerim serve` (direct).

## Syntax

```bash
lerim status [--json]
```

## Parameters

<div class="param-field">
  <div class="param-header">
    <span class="param-name">--json</span>
    <span class="param-type">boolean</span>
    <span class="param-badge default">default: false</span>
  </div>
  <p class="param-desc">Output structured JSON instead of human-readable text.</p>
</div>

## Examples

### Default output

```bash
lerim status
```

**Output (shape):**

```
Lerim status:
- connected_agents: 2
- memory_count: 59
- sessions_indexed_count: 134
- queue: 3 pending, 128 done, 3 failed
```

### JSON output

=== "Human-readable"

    ```bash
    lerim status
    ```

=== "JSON"

    ```bash
    lerim status --json
    ```

    ```json
    {
      "timestamp": "2026-04-12T08:24:00.000000+00:00",
      "connected_agents": ["claude", "codex"],
      "platforms": [
        {
          "name": "claude",
          "path": "/Users/me/.claude/projects",
          "exists": true,
          "session_count": 120
        }
      ],
      "memory_count": 59,
      "sessions_indexed_count": 134,
      "queue": {
        "pending": 3,
        "running": 0,
        "done": 128,
        "failed": 3
      },
      "latest_sync": null,
      "latest_maintain": null
    }
    ```

## Exit codes

| Code | Meaning |
|------|---------|
| `0` | Success |
| `1` | Server not running or unreachable |
| `2` | Usage error |

## Related commands

<div class="grid cards" markdown>

-   :material-sync: **lerim sync**

    ---

    Index and extract new memories

    [:octicons-arrow-right-24: lerim sync](sync.md)

-   :material-wrench: **lerim maintain**

    ---

    Offline memory refinement

    [:octicons-arrow-right-24: lerim maintain](maintain.md)

-   :material-monitor-dashboard: **lerim dashboard**

    ---

    Print temporary dashboard notice

    [:octicons-arrow-right-24: lerim dashboard](dashboard.md)

-   :material-brain: **lerim memory**

    ---

    Search, list, and manage memories

    [:octicons-arrow-right-24: lerim memory](memory.md)

</div>
