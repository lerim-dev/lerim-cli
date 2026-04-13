# lerim status

Show runtime health, per-project stream state, and recent sync/maintain activity.

## Overview

`lerim status` is the main operational dashboard for Lerim.

It shows:

- global summary (agents, memory files, indexed sessions, queue)
- per-project stream state (`blocked`, `running`, `queued`, `healthy`, `idle`)
- recent activity timeline (sync + maintain)
- guidance on what to do next

`lerim status --live` renders the same dashboard and refreshes it on an interval.

!!! note
    This command requires a running server. Start it with `lerim up` (Docker) or `lerim serve` (direct).

## Syntax

```bash
lerim status [--scope all|project] [--project NAME] [--live] [--interval SECONDS] [--json]
```

## Parameters

| Parameter | Default | Description |
|---|---|---|
| `--scope` | `all` | Read scope for status: all registered projects, or one project |
| `--project` | -- | Project name/path when `--scope=project` |
| `--live` | off | Live mode; refreshes the same dashboard repeatedly |
| `--interval` | `3.0` | Refresh interval in seconds for `--live` |
| `--json` | off | Emit JSON payload instead of rich terminal output |

## Examples

### Snapshot status

```bash
lerim status
```

### Live status

```bash
lerim status --live
lerim status --live --interval 1.5
```

### One project only

```bash
lerim status --scope project --project lerim-cli
```

### JSON payload

```bash
lerim status --json
```

JSON includes `projects[]`, `recent_activity[]`, `queue`, `queue_health`, `unscoped_sessions`, and latest run metadata.

## Status states

- `blocked`: oldest project job is `dead_letter`; stream is paused until retry/skip
- `running`: at least one job is currently processing
- `queued`: jobs are waiting and stream is not blocked
- `healthy`: queue empty and project has extracted memory
- `idle`: queue empty and no extracted memory yet

## Typical unblock flow

```bash
lerim queue --failed
lerim retry <run_id>
# or
lerim skip <run_id>
```

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success |
| `1` | Server not running/unreachable, or invalid project selection |
| `2` | Usage error |

## Related commands

<div class="grid cards" markdown>

-   :material-format-list-bulleted: **lerim queue**

    ---

    Inspect queue jobs and failures

    [:octicons-arrow-right-24: lerim queue](overview.md)

-   :material-alert-circle-outline: **lerim unscoped**

    ---

    Show indexed sessions without project mapping

    [:octicons-arrow-right-24: CLI overview](overview.md)

-   :material-sync: **lerim sync**

    ---

    Trigger sync/extraction

    [:octicons-arrow-right-24: lerim sync](sync.md)

</div>
