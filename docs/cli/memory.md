# lerim memory

Manage the memory store directly — list and reset memories.

## Overview

The `memory` command group provides direct access to the memory store. Memories are stored as markdown files in `.lerim/memory/` within each registered project. Use these subcommands to browse or wipe memories.

!!! tip "Direct file access"
    You can also read `.lerim/memory/index.md` directly — it lists all memory files by category with one-line descriptions and links. No server or CLI needed.

---

## memory list

List stored memories as a sorted file list. No server required.

```bash
lerim memory list [--project NAME] [--limit N] [--json]
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--project` | -- | Reserved for project filter (not yet implemented) |
| `--limit` | `50` | Maximum items to display |
| `--json` | off | Output structured JSON |

```bash
lerim memory list
lerim memory list --limit 10 --json
```

---

## memory reset

Irreversibly delete `memory/`, `workspace/`, and `index/` under the selected scope.

```bash
lerim memory reset --yes [--scope SCOPE]
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--yes` | *(required)* | Safety flag -- command refuses to run without it |
| `--scope` | `both` | What to reset: `project`, `global`, or `both` |

!!! danger
    This operation is **irreversible**. All memories, workspace artifacts, and index data within the selected scope will be permanently deleted.

!!! warning
    `--scope project` alone does **not** reset the session queue. The sessions DB lives in `~/.lerim/index/sessions.sqlite3`. Use `--scope global` or `--scope both` to fully reset.

```bash
lerim memory reset --yes                          # wipe everything
lerim memory reset --scope project --yes          # project data only
lerim memory reset --yes && lerim sync --max-sessions 5  # fresh start
```

---

## Exit codes

| Code | Meaning |
|------|---------|
| `0` | Success |
| `1` | Runtime failure (server not running, write error) |
| `2` | Usage error (missing required flags) |

---

## Related commands

<div class="grid cards" markdown>

-   :material-magnify: **lerim ask**

    ---

    Query memories with natural language

    [:octicons-arrow-right-24: lerim ask](ask.md)

-   :material-sync: **lerim sync**

    ---

    Extract memories from agent sessions

    [:octicons-arrow-right-24: lerim sync](sync.md)

-   :material-wrench: **lerim maintain**

    ---

    Offline memory refinement and deduplication

    [:octicons-arrow-right-24: lerim maintain](maintain.md)

-   :material-chart-box: **lerim status**

    ---

    Check memory counts and server state

    [:octicons-arrow-right-24: lerim status](status.md)

</div>
