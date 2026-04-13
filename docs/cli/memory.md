# lerim memory

Manage memory files directly.

## Overview

The `memory` command group provides local memory operations:

- `memory list` to browse memory markdown files
- `memory reset` to wipe selected memory/index roots

`memory list` is local and does not require the server.

---

## memory list

List memory files from all projects (default) or one project.

```bash
lerim memory list [--scope all|project] [--project NAME] [--limit N] [--json]
```

| Parameter | Default | Description |
|---|---|---|
| `--scope` | `all` | Read from all registered projects, or one project |
| `--project` | -- | Project name/path when `--scope=project` |
| `--limit` | `50` | Maximum number of files to print |
| `--json` | off | Print JSON array instead of plain text |

Examples:

```bash
lerim memory list
lerim memory list --scope project --project lerim-cli --limit 20
lerim memory list --json
```

---

## memory reset

Irreversibly delete memory/workspace/index data in selected scope.

```bash
lerim memory reset --yes [--scope project|global|both]
```

| Parameter | Default | Description |
|---|---|---|
| `--yes` | required | Safety confirmation flag |
| `--scope` | `both` | Reset `project`, `global`, or `both` |

!!! danger
    This is permanent. Deleted data cannot be recovered.

!!! warning
    `--scope project` does **not** reset the session queue DB. Queue state lives in global `~/.lerim/index/sessions.sqlite3`. Use `--scope global` or `--scope both` for full queue/index reset.

Examples:

```bash
lerim memory reset --yes
lerim memory reset --scope project --yes
lerim memory reset --scope global --yes
```

---

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success |
| `1` | Runtime failure |
| `2` | Usage error |

---

## Related commands

<div class="grid cards" markdown>

-   :material-brain: **lerim ask**

    ---

    Query memories in natural language

    [:octicons-arrow-right-24: lerim ask](ask.md)

-   :material-sync: **lerim sync**

    ---

    Extract new memories from sessions

    [:octicons-arrow-right-24: lerim sync](sync.md)

-   :material-chart-box: **lerim status**

    ---

    Check memory counts and queue state

    [:octicons-arrow-right-24: lerim status](status.md)

</div>
