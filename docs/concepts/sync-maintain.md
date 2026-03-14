# Sync & Maintain

Lerim has two runtime paths that keep your memory store accurate and clean:

- **Sync** (hot path) -- processes new agent sessions and extracts memories
- **Maintain** (cold path) -- refines existing memories offline

Both run automatically in the daemon loop and can also be triggered manually.

---

## Sync path

The sync path turns raw agent session transcripts into structured memories:

1. **Discover** -- adapters scan session directories for new sessions within the time window
2. **Index** -- new sessions are cataloged in `sessions.sqlite3`
3. **Match to project** -- sessions matching a registered project are enqueued; unmatched sessions are indexed but not extracted
4. **Compact and extract** -- traces are compacted (tool outputs stripped), then DSPy extracts decision/learning candidates
5. **Deduplicate** -- the lead agent compares candidates against existing memories and decides: add, update, or skip
6. **Write** -- new memories and a session summary are saved to `.lerim/memory/`

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

1. **Scan** -- reads all active memories in the project
2. **Merge duplicates** -- combines memories covering the same concept into a single stronger entry
3. **Archive low-value** -- soft-deletes memories with effective confidence below the archive threshold (default: 0.2)
4. **Consolidate** -- combines related memories into richer entries
5. **Apply decay** -- reduces confidence of memories not accessed recently

---

## Memory decay

Decay keeps the memory store focused on relevant knowledge by reducing confidence of unaccessed memories over time.

| Parameter | Default | Description |
|-----------|---------|-------------|
| `decay_days` | `180` | Days of no access before full decay |
| `min_confidence_floor` | `0.1` | Decay never drops below this |
| `archive_threshold` | `0.2` | Below this, memory is archived during maintain |
| `recent_access_grace_days` | `30` | Recently accessed memories skip archiving |

```toml
[memory.decay]
enabled = true
decay_days = 180
min_confidence_floor = 0.1
archive_threshold = 0.2
recent_access_grace_days = 30
```

!!! tip "Accessing memories resets decay"
    Querying memories with `lerim ask` or `lerim memory search` updates access timestamps. Frequently useful memories naturally stay alive.

---

## Automatic scheduling

The daemon runs sync and maintain on independent intervals:

| Path | Config key | Default |
|------|------------|---------|
| Sync | `sync_interval_minutes` | `10` |
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

    Primitives, lifecycle, and confidence decay.

    [:octicons-arrow-right-24: Memory model](memory-model.md)

-   :material-tune:{ .lg .middle } **Configuration**

    ---

    Full TOML config reference including intervals and decay.

    [:octicons-arrow-right-24: Configuration](../configuration/overview.md)

</div>
