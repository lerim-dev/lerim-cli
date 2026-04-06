# Lerim CLI Reference (Source Of Truth)

Canonical parser source:
- `src/lerim/server/cli.py`

Canonical command:
- `lerim`

Commands that call the HTTP API (`ask`, `sync`, `maintain`, `status`) require a
running server (`lerim up` or `lerim serve`). Most other commands are **host-only**
(local files / Docker CLI / queue DB). `memory list` and `memory reset` work without a server.

## Global flags

```bash
--json       # Emit structured JSON instead of human-readable text
--version    # Show version and exit
```

## Exit codes

- `0`: success
- `1`: runtime failure
- `2`: usage error
- `3`: partial success
- `4`: lock busy

## Command map

- `init` (host-only)
- `project` (`add`, `list`, `remove`) (host-only)
- `up` / `down` / `logs` (host-only)
- `serve` (Docker entrypoint, or run directly)
- `connect`
- `sync`
- `maintain`
- `dashboard`
- `memory` (`list`, `reset`)
- `ask`
- `status`
- `queue`
- `retry`
- `skip`
- `skill` (`install`) (host-only)
- `auth` (`login`, `status`, `logout`, or bare `lerim auth`)

## Commands

### `lerim init` (host-only)

Interactive setup wizard. Detects installed coding agents, writes config to
`~/.lerim/config.toml`.

```bash
lerim init
```

### `lerim project` (host-only)

Manage tracked repositories. Each project gets a `.lerim/` directory.

```bash
lerim project add ~/codes/my-app       # register a project
lerim project add .                     # register current directory
lerim project list                      # show all registered projects
lerim project remove my-app             # unregister a project
```

Adding/removing a project restarts the Docker container if running.

### `lerim up` / `lerim down` (host-only)

Docker container lifecycle.

```bash
lerim up                    # start Lerim (pull GHCR image)
lerim up --build            # build from local Dockerfile instead
lerim down                  # stop it
```

| Flag | Default | Description |
|------|---------|-------------|
| `--build` | off | Build from local Dockerfile instead of pulling the GHCR image |

### `lerim logs` (host-only)

View local log entries from `~/.lerim/logs/lerim.jsonl` (last 50 by default).

```bash
lerim logs                      # show recent logs
lerim logs --follow             # tail logs continuously
lerim logs --level error        # filter by level
lerim logs --since 2h           # entries from the last 2 hours
lerim logs --json               # raw JSONL output
```

| Flag | Default | Description |
|------|---------|-------------|
| `--follow`, `-f` | off | Live tail: watch for new log lines |
| `--level` | -- | Filter by log level (case-insensitive): error, warning, info |
| `--since` | -- | Show entries from the last N hours/minutes/days (e.g. `1h`, `30m`, `2d`) |
| `--json` | off | Output raw JSONL lines instead of formatted text |

### `lerim serve`

JSON HTTP API + daemon loop in one process (Docker entrypoint). The **web UI**
is **[Lerim Cloud](https://lerim.dev)** — not bundled in this repo. GET `/` may
return a stub page linking to Cloud when no static assets are present.

```bash
lerim serve
lerim serve --host 0.0.0.0 --port 8765  # custom bind
```

### `lerim connect`

Register, list, or remove agent platform connections.
Lerim reads session data from connected platforms to build memory.

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
| `extra_arg` | Used with `remove` -- the platform to disconnect |
| `--path` | Custom filesystem path to the platform's session store |

### `lerim sync`

Hot-path: discover new agent sessions from connected platforms, enqueue them,
and run DSPy extraction to create memories.
Requires a running server (`lerim up` or `lerim serve`).

**Time window** controls which sessions to scan:
- `--window <duration>` -- relative window like `7d`, `24h`, `30m` (default: from config, `7d`)
- `--window all` -- scan all sessions ever recorded
- `--since` / `--until` -- absolute ISO-8601 bounds (overrides `--window`)

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
lerim sync --ignore-lock            # skip writer lock (debugging only)
```

| Flag | Default | Description |
|------|---------|-------------|
| `--run-id` | -- | Target a single session by run ID (bypasses index scan) |
| `--agent` | all | Comma-separated platform filter (e.g. `claude,codex`) |
| `--window` | config `sync_window_days` (`7d`) | Relative time window (`30s`, `2m`, `1h`, `7d`, or `all`) |
| `--since` | -- | ISO-8601 start bound (overrides `--window`) |
| `--until` | now | ISO-8601 end bound (only with `--since`) |
| `--max-sessions` | config `sync_max_sessions` (`50`) | Max sessions to extract per run |
| `--no-extract` | off | Index/enqueue only, skip extraction |
| `--force` | off | Re-extract already-processed sessions |
| `--dry-run` | off | Preview mode, no writes |
| `--ignore-lock` | off | Skip writer lock (risk of corruption) |

Notes:
- `sync` is the hot path (queue + DSPy extraction + lead write).
- Cold maintenance work is not executed in `sync`.

### `lerim maintain`

Cold-path: offline memory refinement. Scans existing memories and merges
duplicates, archives low-value items, and consolidates related memories.
Archived items go to `memory/archived/`.
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

### Background sync and maintain

There is **no** separate `lerim daemon` command. The daemon loop (sync + maintain
on `sync_interval_minutes` / `maintain_interval_minutes`) runs **inside**
`lerim serve` and therefore inside `lerim up` (Docker).

### `lerim dashboard`

Shows that the web UI has moved to **Lerim Cloud** (`https://lerim.dev`)
and lists CLI alternatives for common tasks.

```bash
lerim dashboard
```

### `lerim memory`

Subcommands for managing the memory store directly.
Memories are stored as markdown files in `.lerim/memory/`.

#### `lerim memory list`

List stored memories, ordered by recency.

```bash
lerim memory list
lerim memory list --limit 10
lerim memory list --json       # structured JSON output
```

| Flag | Default | Description |
|------|---------|-------------|
| `--project` | -- | Filter to project (not yet implemented) |
| `--limit` | `50` | Max items |

#### `lerim memory reset`

Irreversibly delete `memory/`, `workspace/`, and `index/` under selected scope.

Scopes:
- `project` -- reset `<repo>/.lerim/` only
- `global` -- reset `~/.lerim/` only (includes sessions DB)
- `both` -- reset both project and global roots (default)

The sessions DB lives in global `index/`, so `--scope project` alone does **not** reset the session queue. Use `--scope global` or `--scope both` to fully reset sessions.

```bash
lerim memory reset --yes                     # wipe everything (both scopes)
lerim memory reset --scope project --yes     # project data only
lerim memory reset --yes && lerim sync --max-sessions 5  # fresh start
```

| Flag | Default | Description |
|------|---------|-------------|
| `--scope` | `both` | `project`, `global`, or `both` |
| `--yes` | off | Required safety flag (refuses to run without it) |

### `lerim ask`

One-shot query: ask Lerim a question with memory-informed context.
Requires a running server (`lerim up` or `lerim serve`).

```bash
lerim ask 'What auth pattern do we use?'
lerim ask "How is the database configured?" --limit 5
```

| Flag | Default | Description |
|------|---------|-------------|
| `question` | required | Your question (quote if spaces) |
| `--project` | -- | Scope to project (not yet implemented) |
| `--limit` | `12` | Max memory items as context |

Notes:
- Ask uses memory retrieval evidence.
- If provider auth fails, CLI returns exit code 1.

### `lerim status`

Print runtime state: connected platforms, memory count, session queue stats,
and timestamps of the latest sync/maintain runs.
Requires a running server (`lerim up` or `lerim serve`).

```bash
lerim status
lerim status --json    # structured JSON output
```

### `lerim queue`

Host-only: reads the session extraction queue from the local SQLite catalog (no HTTP).

```bash
lerim queue
lerim queue --failed
lerim queue --status pending
lerim queue --project lerim-cli
lerim queue --json
```

| Flag | Description |
|------|-------------|
| `--failed` | Only failed + dead_letter jobs |
| `--status` | Filter by status (`pending`, `running`, `failed`, `dead_letter`, `done`) |
| `--project` | Substring match on repo path |

### `lerim retry`

Host-only: reset `dead_letter` jobs to `pending` so the daemon can re-process them.

```bash
lerim retry a1b2c3d4
lerim retry --project lerim-cli
lerim retry --all
```

### `lerim skip`

Host-only: mark `dead_letter` jobs as done (skipped) to unblock the queue.

```bash
lerim skip a1b2c3d4
lerim skip --project lerim-cli
lerim skip --all
```

### `lerim auth`

Authenticate with Lerim Cloud (browser login, token, status, logout).

```bash
lerim auth                    # browser OAuth (default)
lerim auth --token lerim_tok_...
lerim auth login
lerim auth status
lerim auth logout
```

### `lerim skill` (host-only)

Install Lerim skill files into coding agent directories.

Installs to two locations:
- `~/.agents/skills/lerim/` — shared by Cursor, Codex, OpenCode, and others
- `~/.claude/skills/lerim/` — Claude Code (reads only from its own directory)

```bash
lerim skill install
```
