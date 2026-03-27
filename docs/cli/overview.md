# CLI overview

Global flags, exit codes, and common patterns for Lerim CLI.

The Lerim CLI is the primary interface for managing Lerim's continual learning layer. Commands fall into two categories:

- **Host-only commands** run locally and do not call the HTTP API: `init`, `project`, `up`, `down`, `logs`, `connect`, `memory` (all subcommands: `search`, `list`, `add`, `reset`), `dashboard`, `queue`, `retry`, `skip`, `skill`, `auth`
- **Service commands** forward to `lerim serve` via HTTP and require a running server (`lerim up` or `lerim serve`): `ask`, `sync`, `maintain`, `status`

`memory search`, `memory list`, and `memory add` read or write the memory tree on disk directly (no server). The background sync/maintain loop runs **inside** `lerim serve` — there is no separate `lerim daemon` command (see [Background loop](daemon.md)).

## Installation

```bash
pip install lerim
```

!!! note "Prerequisites"
    Python 3.10+, Docker (optional).

## Quick start

```bash
lerim init                # interactive setup — detects your coding agents
lerim project add .       # add current project
lerim connect auto        # connect all detected platforms
lerim up                  # start Docker service
lerim ask "your question" # query memories
```

## Global flags

These flags work with most commands:

```bash
--json       # Emit structured JSON instead of human-readable text
--version    # Show version and exit
```

!!! info
    `--json` flags must appear before or after the subcommand itself. `lerim --json sync`, `lerim sync --json`, `lerim status --json`, and `lerim memory list --json` all work.

## Exit codes

Lerim commands return standard exit codes:

| Code | Meaning | Example |
|------|---------|---------|
| `0` | Success | Command completed without errors |
| `1` | Runtime failure | Server not reachable, LLM API error |
| `2` | Usage error | Invalid arguments, missing required parameters |
| `3` | Partial success | Some sessions processed, others failed |
| `4` | Lock busy | Another process holds the sync/maintain lock |

## Command categories

### Setup and project management

- `lerim init` — Interactive setup wizard
- `lerim project add` — Register a project directory
- `lerim project list` — List registered projects
- `lerim project remove` — Unregister a project

### Service lifecycle

- `lerim up` — Start Docker container
- `lerim down` — Stop Docker container
- `lerim logs` — View container logs
- `lerim serve` — Run HTTP server + daemon (Docker entrypoint)

### Memory operations

- `lerim sync` — Index sessions and extract memories (hot path)
- `lerim maintain` — Refine existing memories (cold path)
- `lerim ask` — Query memories with natural language
- `lerim queue` — Show session extraction queue (host-only, SQLite)
- `lerim retry` / `lerim skip` — Manage dead-letter jobs (host-only)

### Platform connections

- `lerim connect` — Manage agent platform connections
- `lerim connect auto` — Auto-detect and connect all platforms
- `lerim connect list` — Show connected platforms

### Direct memory access

- `lerim memory search` — Full-text search across memories
- `lerim memory list` — List stored memory files
- `lerim memory add` — Manually create a memory
- `lerim memory reset` — Destructive wipe of memory data

### Skills

- `lerim skill install` — Install Lerim skill files for coding agents

### Runtime status

- `lerim status` — Show runtime state (requires server)
- `lerim dashboard` — Print local API URL + Lerim Cloud hint (host-only)

### Cloud

- `lerim auth` — Lerim Cloud login, status, logout

## Common patterns

### First-time setup

```bash
lerim init                    # configure agents
lerim project add ~/codes/app # register projects
lerim connect auto            # connect all platforms
lerim up                      # start service
```

### Daily workflow

```bash
# Query after a coding session
lerim ask "What auth pattern are we using?"

# After many sessions, run extraction if needed
lerim sync --max-sessions 10

# View status
lerim status
```

### Troubleshooting

```bash
# Check if service is running
lerim status

# View logs
lerim logs --follow

# Restart service
lerim down && lerim up

# If nothing is printing
lerim logs --follow
```

### Fresh start

```bash
# Reinitialize config (preserves memories)
lerim init

# Or wipe everything and start clean
lerim memory reset --scope both --yes
lerim down
lerim up
```

!!! warning
    `lerim memory reset` is **permanent**. It deletes all memories, workspace data, and session indexes. This cannot be undone.

## Running without Docker

If you prefer not to use Docker, run Lerim directly:

```bash
lerim connect auto           # detect agent platforms
lerim serve                  # JSON API + daemon loop
```

Then use `lerim ask`, `lerim sync`, `lerim status`, etc. as usual.

## Configuration

Lerim uses TOML layered configuration (lowest to highest priority):

```bash
src/lerim/config/default.toml    # shipped defaults
~/.lerim/config.toml              # user global overrides
<repo>/.lerim/config.toml        # project overrides
LERIM_CONFIG env var              # explicit override path
```

API keys come from environment variables:

Keys depend on `[roles.*]` (see shipped `src/lerim/config/default.toml`). Examples:

- `OPENCODE_API_KEY` — OpenCode Go / Zen (common in current defaults)
- `MINIMAX_API_KEY`, `ZAI_API_KEY` — when using those providers
- `OPENROUTER_API_KEY`, `OPENAI_API_KEY`, … — as configured

Only the keys for providers you use are required.

## Next steps

<div class="grid cards" markdown>

-   :material-play-circle: **lerim init**

    ---

    Run the interactive setup wizard

    [:octicons-arrow-right-24: lerim init](init.md)

-   :material-folder-plus: **lerim project**

    ---

    Register and track your repositories

    [:octicons-arrow-right-24: lerim project](project.md)

-   :material-connection: **Connect platforms**

    ---

    Link your coding agents

    [:octicons-arrow-right-24: lerim connect](connect.md)

-   :material-sync: **Sync sessions**

    ---

    Extract memories from agent sessions

    [:octicons-arrow-right-24: lerim sync](sync.md)

</div>
