# lerim status

Print runtime state: connected platforms, memory count, session queue stats, and latest run timestamps.

## Overview

A quick health-check for your Lerim instance. Shows what platforms are connected, how many memories exist, session queue depth, and when sync/maintain last ran.

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

**Output:**

```
Lerim v0.4.0  —  running on http://localhost:8765

Platforms:
  claude   connected   ~/.claude/projects/
  codex    connected   ~/.codex/sessions/
  cursor   not connected

Projects: 2 registered
  ~/codes/my-app       42 memories (28 learnings, 14 decisions)
  ~/codes/backend      17 memories (12 learnings, 5 decisions)

Session queue:
  total indexed:  134
  pending:          3
  processed:      128
  failed:           3

Last runs:
  sync:      2026-02-28 14:32:00  (7 min ago)
  maintain:  2026-02-28 13:45:00  (54 min ago)
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
      "version": "0.4.0",
      "server_url": "http://localhost:8765",
      "platforms": {
        "claude": {"connected": true, "path": "~/.claude/projects/"},
        "codex": {"connected": true, "path": "~/.codex/sessions/"},
        "cursor": {"connected": false, "path": null}
      },
      "projects": [
        {
          "path": "~/codes/my-app",
          "memories": {"total": 42, "learnings": 28, "decisions": 14}
        }
      ],
      "session_queue": {
        "total": 134,
        "pending": 3,
        "processed": 128,
        "failed": 3
      },
      "last_sync": "2026-02-28T14:32:00Z",
      "last_maintain": "2026-02-28T13:45:00Z"
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

    Print API URL + Lerim Cloud

    [:octicons-arrow-right-24: lerim dashboard](dashboard.md)

-   :material-brain: **lerim memory**

    ---

    Search, list, and manage memories

    [:octicons-arrow-right-24: lerim memory](memory.md)

</div>
