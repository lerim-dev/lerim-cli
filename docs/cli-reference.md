# CLI Reference

All commands are available via the `lerim` entry point.

Service commands (`ask`, `sync`, `maintain`, `status`) are thin HTTP clients that
require a running server (`lerim up` or `lerim serve`). Commands marked **(host-only)**
always run on the host machine.

## Global flags

```bash
--json       # Emit structured JSON instead of human-readable text
--version    # Show version and exit
```

## Exit codes

| Code | Meaning |
|------|---------|
| `0` | Success |
| `1` | Runtime failure |
| `2` | Usage error |
| `3` | Partial success |
| `4` | Lock busy |

---

## `lerim init` (host-only)

Interactive setup wizard. Detects installed coding agents, lets you select which
to connect, and writes the initial config to `~/.lerim/config.toml`.

```bash
lerim init
```

Run this once after installing Lerim. It checks for Docker and prints next steps.

---

## `lerim project` (host-only)

Manage which repositories Lerim tracks. Each project gets a `.lerim/` directory
for its memories.

```bash
lerim project add ~/codes/my-app       # register a project
lerim project add .                     # register current directory
lerim project list                      # show all registered projects
lerim project remove my-app             # unregister a project
```

Adding or removing a project restarts the Docker container if it is running
(to update volume mounts).

---

## `lerim up` / `lerim down` / `lerim logs` (host-only)

Docker container lifecycle management.

```bash
lerim up                    # start Lerim (Docker container)
lerim down                  # stop it
lerim logs                  # tail logs
lerim logs --follow         # follow logs continuously
```

`lerim up` reads `~/.lerim/config.toml`, generates a `docker-compose.yml` in
`~/.lerim/`, and runs `docker compose up -d`. The container runs `lerim serve`
(daemon + API + dashboard). Running `lerim up` again recreates the container.

---

## `lerim serve`

Starts the HTTP API server, dashboard, and daemon loop in a single process.
This is the Docker container entrypoint, but can also be run directly for
development without Docker.

```bash
lerim serve                              # start everything
lerim serve --host 0.0.0.0 --port 8765  # custom bind
```

| Flag | Default | Description |
|------|---------|-------------|
| `--host` | `0.0.0.0` | Bind address |
| `--port` | `8765` | Bind port |

---

## `lerim connect`

Register, list, or remove agent platform connections. Lerim reads session data from connected platforms to build memory.

Supported platforms: `claude`, `codex`, `cursor`, `opencode`

```bash
lerim connect list                        # show all connected platforms
lerim connect auto                        # auto-detect and connect all known platforms
lerim connect claude                      # connect the Claude platform
lerim connect claude --path /custom/dir   # connect with custom session store path
lerim connect remove claude               # disconnect Claude
```

| Flag | Description |
|------|-------------|
| `platform_name` | Action or platform: `list`, `auto`, `remove`, or a platform name |
| `extra_arg` | Used with `remove` — the platform to disconnect |
| `--path` | Custom filesystem path to the platform's session store |

---

## `lerim sync`

Hot-path: discover new agent sessions from connected platforms, enqueue them, and run DSPy extraction to create memory primitives.
Requires a running server (`lerim up` or `lerim serve`).

### Time window

- `--window <duration>` — relative window like `7d`, `24h`, `30m` (default: from config, `7d`)
- `--window all` — scan all sessions ever recorded
- `--since` / `--until` — absolute ISO-8601 bounds (overrides `--window`)

Duration format: `<number><unit>` where unit is `s` (seconds), `m` (minutes), `h` (hours), `d` (days).

```bash
lerim sync                          # sync using configured window (default: 7d)
lerim sync --window 30d             # sync last 30 days
lerim sync --window all             # sync everything
lerim sync --agent claude,codex     # only sync these platforms
lerim sync --run-id abc123 --force  # re-extract a specific session
lerim sync --since 2026-02-01T00:00:00Z --until 2026-02-08T00:00:00Z
lerim sync --no-extract             # index and enqueue only, skip extraction
lerim sync --dry-run                # preview what would happen, no writes
lerim sync --max-sessions 100       # process up to 100 sessions
```

| Flag | Default | Description |
|------|---------|-------------|
| `--run-id` | — | Target a single session by run ID (bypasses index scan) |
| `--agent` | all | Comma-separated platform filter (e.g. `claude,codex`) |
| `--window` | config `sync_window_days` (`7d`) | Relative time window (`30s`, `2m`, `1h`, `7d`, or `all`) |
| `--since` | — | ISO-8601 start bound (overrides `--window`) |
| `--until` | now | ISO-8601 end bound (only with `--since`) |
| `--max-sessions` | config `sync_max_sessions` (`50`) | Max sessions to extract per run |
| `--no-extract` | off | Index/enqueue only, skip extraction |
| `--force` | off | Re-extract already-processed sessions |
| `--dry-run` | off | Preview mode, no writes |
| `--ignore-lock` | off | Skip writer lock (risk of corruption) |

!!! note
    `sync` is the hot path (queue + DSPy extraction + lead decision/write). Cold maintenance work is not executed in `sync`.

---

## `lerim maintain`

Cold-path: offline memory refinement. Scans existing memories and merges duplicates, archives low-value items, and consolidates related memories. Archived items go to `memory/archived/{decisions,learnings}/`.
Requires a running server (`lerim up` or `lerim serve`).

```bash
lerim maintain                # run one maintenance pass
lerim maintain --force        # force maintenance even if recently run
lerim maintain --dry-run      # preview only, no writes
```

| Flag | Description |
|------|-------------|
| `--force` | Force maintenance even if a recent run was completed |
| `--dry-run` | Record a run but skip actual memory changes |

---

## `lerim daemon`

Runs a continuous loop with independent sync and maintain intervals. Sync (hot path) runs frequently; maintain (cold path) runs less often. Sessions are processed in parallel using a thread pool (configurable via `sync_max_workers`, default 4).

```bash
lerim daemon                     # run forever (sync every 10 min, maintain every 60 min)
lerim daemon --once              # run one sync+maintain cycle and exit
lerim daemon --poll-seconds 120  # override both intervals uniformly to 2 minutes
```

| Flag | Default | Description |
|------|---------|-------------|
| `--once` | off | Run one cycle and exit |
| `--poll-seconds` | — | Override both sync and maintain intervals uniformly (seconds, minimum 30s) |

Default intervals come from config: `sync_interval_minutes` (10) and `maintain_interval_minutes` (60).

---

## `lerim dashboard`

Prints the dashboard URL. The dashboard itself is served by `lerim serve`
(or `lerim up`).

```bash
lerim dashboard                  # print the dashboard URL
```

See [Dashboard](dashboard.md) for details on the web UI.

---

## `lerim memory`

Subcommands for managing the memory store directly. Memories are stored as markdown files in `.lerim/memory/`.

### `lerim memory search`

Full-text keyword search across memory titles, bodies, and tags (case-insensitive).

```bash
lerim memory search 'database migration'
lerim memory search pytest --limit 5
```

| Flag | Default | Description |
|------|---------|-------------|
| `query` | required | Search string to match |
| `--limit` | `20` | Max results |

### `lerim memory list`

List stored memories (decisions and learnings), ordered by recency.

```bash
lerim memory list
lerim memory list --limit 10
lerim memory list --json       # structured JSON output
```

| Flag | Default | Description |
|------|---------|-------------|
| `--limit` | `50` | Max items |

### `lerim memory add`

Manually create a single memory record.

```bash
lerim memory add --title "Use uv for deps" --body "uv is faster than pip"
lerim memory add --title "API auth" --body "Use bearer tokens" --primitive decision
lerim memory add --title "Slow test" --body "Integration suite 5min" \
    --kind friction --confidence 0.9 --tags ci,testing
```

| Flag | Default | Description |
|------|---------|-------------|
| `--title` | required | Short descriptive title |
| `--body` | required | Full body content |
| `--primitive` | `learning` | `decision` or `learning` |
| `--kind` | `insight` | `insight`, `procedure`, `friction`, `pitfall`, `preference` |
| `--confidence` | `0.7` | Score from 0.0 to 1.0 |
| `--tags` | — | Comma-separated tags (e.g. `python,testing,ci`) |

### `lerim memory reset`

Irreversibly delete `memory/`, `workspace/`, and `index/` under selected scope.

```bash
lerim memory reset --yes                     # wipe everything (both scopes)
lerim memory reset --scope project --yes     # project data only
lerim memory reset --yes && lerim sync --max-sessions 5  # fresh start
```

| Flag | Default | Description |
|------|---------|-------------|
| `--scope` | `both` | `project`, `global`, or `both` |
| `--yes` | off | Required safety flag (refuses to run without it) |

!!! warning
    `--scope project` alone does **not** reset the session queue. The sessions DB lives in global `index/`. Use `--scope global` or `--scope both` to fully reset sessions.

---

## `lerim ask`

One-shot query: ask Lerim a question with memory-informed context.
Requires a running server (`lerim up` or `lerim serve`).

```bash
lerim ask 'What auth pattern do we use?'
lerim ask "How is the database configured?" --limit 5
```

| Flag | Default | Description |
|------|---------|-------------|
| `question` | required | Your question (quote if spaces) |
| `--limit` | `12` | Max memory items as context |

---

## `lerim status`

Print runtime state: connected platforms, memory count, session queue stats, and timestamps of the latest sync/maintain runs.
Requires a running server (`lerim up` or `lerim serve`).

```bash
lerim status
lerim status --json    # structured JSON output
```
