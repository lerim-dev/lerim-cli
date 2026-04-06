# lerim dashboard

Shows that the web UI has moved to **Lerim Cloud** and lists CLI alternatives.

## Overview

The browser UI is hosted separately at **[lerim.dev](https://lerim.dev)** (not yet available). This command prints a transition message and lists CLI commands you can use in the meantime.

## Syntax

```bash
lerim dashboard
```

## Examples

```bash
lerim dashboard
```

Sample output:

```
  Lerim Dashboard is moving to the cloud.
  The new dashboard will be available at https://lerim.dev

  In the meantime, use these CLI commands:
    lerim status     - system overview
    lerim ask        - query your memories
    lerim queue      - view session processing queue
    lerim sync       - process new sessions
    lerim maintain   - run memory maintenance
```

## See also

- [lerim status](status.md) — runtime state overview
- [lerim serve](serve.md) — HTTP API + daemon loop
- [Web UI (Lerim Cloud)](../guides/dashboard.md)
