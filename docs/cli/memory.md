# lerim memory

Manage the memory store directly — search, list, add, and reset memories.

## Overview

The `memory` command group provides direct access to the memory store. Memories are stored as markdown files in `.lerim/memory/` within each registered project. Use these subcommands to search, browse, manually create, or wipe memories.

!!! note
    Subcommands that read memory (`search`, `list`) require a running server. Start it with `lerim up` (Docker) or `lerim serve` (direct).

---

## memory search

Full-text keyword search across memory titles, bodies, and tags.

```bash
lerim memory search <query> [--project NAME] [--limit N]
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `query` | *(required)* | Search string to match against memories |
| `--project` | *(auto)* | Target project name |
| `--limit` | `20` | Maximum results to return |

```bash
lerim memory search 'database migration'
lerim memory search pytest --limit 5
```

---

## memory list

List stored memories (decisions and learnings), ordered by recency.

```bash
lerim memory list [--project NAME] [--limit N] [--json]
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--project` | *(auto)* | Target project name |
| `--limit` | `50` | Maximum items to display |
| `--json` | off | Output structured JSON |

```bash
lerim memory list
lerim memory list --limit 10 --json
```

---

## memory add

Manually create a memory record. Useful for codifying decisions or learnings that didn't come from an agent session.

```bash
lerim memory add --title <TITLE> --body <BODY> [options]
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--title` | *(required)* | Short descriptive title |
| `--body` | *(required)* | Full body content |
| `--primitive` | `learning` | Type: `decision` or `learning` |
| `--kind` | `insight` | Kind: `insight`, `procedure`, `friction`, `pitfall`, `preference` |
| `--confidence` | `0.7` | Confidence score (0.0 to 1.0) |
| `--tags` | *(none)* | Comma-separated tags (e.g. `python,testing,ci`) |

```bash
# Add a simple learning
lerim memory add --title "Use uv for deps" --body "uv is faster than pip"

# Add a decision
lerim memory add --title "API auth" --body "Use bearer tokens" --primitive decision

# Full options
lerim memory add \
    --title "Slow integration tests" \
    --body "Integration suite takes 5 min" \
    --kind friction \
    --confidence 0.9 \
    --tags ci,testing
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
