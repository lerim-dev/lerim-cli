# Sync & Maintain

Lerim has two runtime paths that keep your memory store accurate and clean:

- **Sync** (hot path) -- processes new agent sessions and extracts memories
- **Maintain** (cold path) -- refines existing memories offline

Both run automatically in the daemon loop and can also be triggered manually.
Both use the same PydanticAI runtime and the `[roles.agent]` role model.

---

## Sync path

The sync path turns raw agent session transcripts into structured memories:

1. **Discover** -- adapters scan session directories for new sessions within the time window
2. **Index** -- new sessions are cataloged in `sessions.sqlite3`
3. **Match to project** -- sessions matching a registered project are enqueued; unmatched sessions are indexed but not extracted
4. **Compact** -- traces are compacted (tool outputs stripped) and cached
5. **Extract flow** -- the PydanticAI extraction agent (`[roles.agent]`) reads the trace and uses memory tools (`read`, `grep`, `note`, `prune`, `write`, `edit`, `verify_index`) to write or edit memories, update `index.md`, and save a session summary

### Time window

| Config key | Default | Description |
|------------|---------|-------------|
| `sync_window_days` | `7` | How far back to look for sessions |
| `sync_max_sessions` | `50` | Maximum sessions per sync cycle |

Override with CLI flags:

```bash
lerim sync --window 14d              # last 14 days
lerim sync --window all              # all sessions ever
lerim sync --max-sessions 10         # limit batch size
```

!!! info "Processing order"
    Sessions are processed in **chronological order** (oldest-first) so that later sessions can correctly update memories from earlier ones.

---

## Maintain path

The maintain path runs offline refinement over stored memories, iterating over all registered projects:

1. **Scan** -- `scan()` and optional reads of summaries / `index.md`
2. **Merge duplicates** -- edit or archive redundant markdown files
3. **Archive low-value** -- `archive()` moves files to `memory/archived/`
4. **Consolidate** -- combine related topics via `edit()` / `write()`
5. **Re-index** -- `verify_index()` checks consistency; the agent uses `edit("index.md", ...)` to refresh the memory index when needed

### Request turn limits

The maintain and ask flows use explicit request-turn budgets from config:

| Flow | Config key | Purpose |
|------|------------|---------|
| Maintain | `max_iters_maintain` | Caps maintain agent request turns per run |
| Ask | `max_iters_ask` | Caps ask agent request turns per query |

---

## Automatic scheduling

The daemon runs sync and maintain on independent intervals:

| Path | Config key | Default (see `default.toml`) |
|------|------------|---------|
| Sync | `sync_interval_minutes` | `30` |
| Maintain | `maintain_interval_minutes` | `60` |

Both trigger immediately on daemon startup, then repeat at their configured intervals.

### Local model memory management

When using Ollama, Lerim automatically loads the model into RAM before each cycle and unloads it after (`auto_unload = true` in `[providers]`). The model only occupies memory during active processing.

### Manual triggers

```bash
lerim sync                           # sync with default settings
lerim sync --run-id <id>             # sync a specific session
lerim sync --dry-run                 # preview without writing
lerim maintain                       # run maintain cycle
lerim maintain --dry-run             # preview without writing
```

---

## Related

<div class="grid cards" markdown>

-   :material-cog:{ .lg .middle } **How It Works**

    ---

    Architecture overview and deployment model.

    [:octicons-arrow-right-24: How it works](how-it-works.md)

-   :material-brain:{ .lg .middle } **Memory Model**

    ---

    Types, layout, and lifecycle.

    [:octicons-arrow-right-24: Memory model](memory-model.md)

-   :material-tune:{ .lg .middle } **Configuration**

    ---

    Full TOML config reference including daemon intervals.

    [:octicons-arrow-right-24: Configuration](../configuration/overview.md)

</div>
