# Lerim CLI Reference (Source Of Truth)

Canonical parser source:
- `src/lerim/app/cli.py`

Canonical command:
- `lerim`

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

- `connect`
- `sync`
- `maintain`
- `daemon`
- `dashboard`
- `memory` (`search`, `list`, `add`, `export`, `reset`)
- `chat`
- `status`

## Commands

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
and run DSPy extraction to create memory primitives.

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
- `sync` is the hot path (queue + DSPy extraction + lead decision/write).
- Cold maintenance work is not executed in `sync`.

### `lerim maintain`

Cold-path: offline memory refinement. Scans existing memories and merges
duplicates, archives low-value items, and consolidates related memories.
Archived items go to `memory/archived/{decisions,learnings}/`.

```bash
lerim maintain                # run one maintenance pass
lerim maintain --force        # force maintenance even if recently run
lerim maintain --dry-run      # preview only, no writes
```

| Flag | Description |
|------|-------------|
| `--force` | Force maintenance even if a recent run was completed |
| `--dry-run` | Record a run but skip actual memory changes |

### `lerim daemon`

Runs a continuous loop: sync (index + extract) then maintain (refine),
repeating at a configurable interval. Sessions are processed in parallel
using a thread pool (configurable via `sync_max_workers`, default 4).

```bash
lerim daemon                     # run forever with default poll interval (30 min)
lerim daemon --once              # run one sync+maintain cycle and exit
lerim daemon --poll-seconds 120  # poll every 2 minutes
```

| Flag | Default | Description |
|------|---------|-------------|
| `--once` | off | Run one cycle and exit |
| `--poll-seconds` | config `poll_interval_minutes` (`30` min) | Seconds between cycles (minimum 30s) |

### `lerim dashboard`

Launch a local HTTP dashboard to browse sessions/memories, view pipeline status,
and update settings (writes to `~/.lerim/config.toml`).

```bash
lerim dashboard                          # start on default host/port
lerim dashboard --host 0.0.0.0 --port 9000  # custom bind address
```

| Flag | Default | Description |
|------|---------|-------------|
| `--host` | `127.0.0.1` | Bind address |
| `--port` | `8765` | Bind port |

### `lerim memory`

Subcommands for managing the memory store directly.
Memories are stored as markdown files in `.lerim/memory/`.

#### `lerim memory search`

Full-text keyword search across memory titles, bodies, and tags (case-insensitive).

```bash
lerim memory search 'database migration'
lerim memory search pytest --limit 5
```

| Flag | Default | Description |
|------|---------|-------------|
| `query` | required | Search string to match |
| `--project` | -- | Filter to project (not yet implemented) |
| `--limit` | `20` | Max results |

#### `lerim memory list`

List stored memories (decisions and learnings), ordered by recency.

```bash
lerim memory list
lerim memory list --limit 10
lerim memory list --json       # structured JSON output
```

| Flag | Default | Description |
|------|---------|-------------|
| `--project` | -- | Filter to project (not yet implemented) |
| `--limit` | `50` | Max items |

#### `lerim memory add`

Manually create a single memory record.

```bash
lerim memory add --title "Use uv for deps" --body "uv is faster than pip"
lerim memory add --title "API auth" --body "Use bearer tokens" --primitive decision
lerim memory add --title "Slow test" --body "Integration suite 5min" --kind friction --confidence 0.9 --tags ci,testing
```

| Flag | Default | Description |
|------|---------|-------------|
| `--title` | required | Short descriptive title |
| `--body` | required | Full body content |
| `--primitive` | `learning` | `decision` or `learning` |
| `--kind` | `insight` | `insight`, `procedure`, `friction`, `pitfall`, `preference` |
| `--confidence` | `0.7` | Score from 0.0 to 1.0 |
| `--tags` | -- | Comma-separated tags (e.g. `python,testing,ci`) |

#### `lerim memory export`

Export every memory record as JSON or markdown.

```bash
lerim memory export                          # markdown to stdout
lerim memory export --format json            # JSON to stdout
lerim memory export --format json --output memories.json
```

| Flag | Default | Description |
|------|---------|-------------|
| `--project` | -- | Filter to project (not yet implemented) |
| `--format` | `markdown` | `json` or `markdown` |
| `--output` | stdout | File path (creates parent dirs) |

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

### `lerim chat`

One-shot query: ask Lerim a question with memory-informed context.

```bash
lerim chat 'What auth pattern do we use?'
lerim chat "How is the database configured?" --limit 5
```

| Flag | Default | Description |
|------|---------|-------------|
| `question` | required | Your question (quote if spaces) |
| `--project` | -- | Scope to project (not yet implemented) |
| `--limit` | `12` | Max memory items as context |

Notes:
- Chat uses memory retrieval evidence.
- If provider auth fails, CLI returns exit code 1.

### `lerim status`

Print runtime state: connected platforms, memory count, session queue stats,
and timestamps of the latest sync/maintain runs.

```bash
lerim status
lerim status --json    # structured JSON output
```
