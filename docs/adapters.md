# Connecting Agents

Lerim ingests session transcripts from your coding agents to extract decisions and learnings. The `lerim connect` command registers an agent platform so Lerim knows where to find its sessions.

## Supported platforms

| Platform | Session store | Format |
|----------|--------------|--------|
| `claude` | `~/.claude/projects/` | JSONL files |
| `codex` | `~/.codex/sessions/` | JSONL files |
| `cursor` | `~/Library/Application Support/Cursor/User/globalStorage/` (macOS) | SQLite `state.vscdb`, exported to JSONL cache |
| `opencode` | `~/.local/share/opencode/` | SQLite `opencode.db`, exported to JSONL cache |

## Auto-detect

Connect all supported platforms in one command:

```bash
lerim connect auto
```

This scans default paths for each platform and registers any that are found.

## Connect individual platforms

```bash
lerim connect claude
lerim connect codex
lerim connect cursor
lerim connect opencode
```

## Custom session paths

If your agent stores sessions in a non-default location:

```bash
lerim connect claude --path /custom/path/to/claude/sessions
lerim connect cursor --path ~/my-cursor-data/globalStorage
```

The path is expanded (`~` is resolved) and must exist on disk. This overrides the auto-detected default for that platform.

## List connections

```bash
lerim connect list
```

## Disconnect a platform

```bash
lerim connect remove claude
```

## How adapters work

Each adapter implements the same protocol:

1. **`default_path()`** — where traces live on disk
2. **`count_sessions(path)`** — how many sessions exist
3. **`iter_sessions(traces_dir, start, end, known_run_ids)`** — yield session records within a time window
4. **`find_session_path(session_id, traces_dir)`** — locate a specific session file
5. **`read_session(session_path, session_id)`** — parse a session for the dashboard viewer

Adapters handle platform-specific formats (JSONL, SQLite) and normalize them into a common `SessionRecord` that the sync pipeline processes.

## Adding a new adapter

See [Contributing](contributing.md) for the step-by-step guide to adding a new platform adapter. Adapters are the easiest contribution path — clear interface, isolated scope.
