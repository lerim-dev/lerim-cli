# How It Works

Lerim is the **continual learning layer** for AI coding agents. It watches your agent sessions, extracts decisions and learnings, and makes that knowledge available to every agent on every future session.

---

## Core principles

<div class="grid cards" markdown>

-   :material-file-document-outline:{ .lg .middle } **File-first**

    ---

    Memories are plain markdown files with YAML frontmatter. No database required -- files are the canonical store. Humans and agents can read them directly.

-   :material-folder-account:{ .lg .middle } **Project-scoped**

    ---

    Each project gets its own `.lerim/` directory. Memories are isolated per-repo so different projects don't mix.

-   :material-transit-connection-variant:{ .lg .middle } **Agent-agnostic**

    ---

    Works with any coding agent that produces session traces. Platform adapters normalize different formats into a common pipeline.

-   :material-refresh:{ .lg .middle } **Self-maintaining**

    ---

    Memories are automatically refined over time -- duplicates merged, stale entries archived, related learnings consolidated.

</div>

---

## Data flow

Lerim has two runtime paths that work together: **sync** (hot path) and **maintain** (cold path).

```mermaid
flowchart TD
    A["Agent sessions\n(any supported coding agent)"] --> B["Adapters\n(normalize to common format)"]
    B --> C["Session catalog\n(index + job queue)"]
    C --> D["Sync path\n(extract memories)"]
    D --> E["Project memory\n(.lerim/memory/)"]
    E --> F["Maintain path\n(refine memories)"]
    F --> E
    E --> G["Query\n(lerim ask / memory search)"]
```

---

## Sync path (hot)

The sync path processes new agent sessions and turns them into memories.

**Agent / tools view** -- `LerimRuntime` runs **ExtractAgent** (DSPy ReAct) with the **`[roles.lead]`** language model. The agent calls methods on `MemoryTools(memory_root, trace_path)` to read the trace, inspect existing memories, write or edit files, and save summaries:

```mermaid
flowchart TB
    subgraph lead["Lead"]
        RT[LerimRuntime · ExtractAgent]
    end
    subgraph lm["LM"]
        L[roles.lead]
    end
    subgraph syncTools["Sync tools (5)"]
        t1["read · grep · scan"]
        wm["write · edit"]
    end
    RT --> L
    RT --> t1
    RT --> wm
```

**Pipeline steps** (ingest + agent run):

1. **Discover** -- adapters scan session directories for new sessions within the time window (default: last 7 days)
2. **Index** -- new sessions are cataloged with metadata (agent type, repo path, timestamps)
3. **Compact** -- traces are compacted by stripping tool outputs and reasoning blocks (typically 40-90% size reduction), cached in `~/.lerim/cache/`
4. **Extract (ReAct)** -- the lead agent reads the transcript and existing memories, then writes high-value items as typed markdown (`user`, `feedback`, `project`, `reference`)
5. **Dedupe** -- happens inside the agent loop via `scan`, `read`, `write`, and `edit` on `MemoryTools`
6. **Summarize** -- `write(type="summary", ...)` stores an episodic summary under `memory/summaries/`

---

## Maintain path (cold)

The maintain path refines existing memories offline.

**Agent / tools view** — same **`[roles.lead]`** model; **MaintainAgent** uses `MemoryTools(memory_root)` (no trace ingestion):

```mermaid
flowchart TB
    subgraph lead_m["Lead"]
        RT_m[LerimRuntime · MaintainAgent]
    end
    subgraph maintainTools["Maintain tools (5)"]
        t2["read · scan"]
        wm2["write · edit"]
        ar[archive]
    end
    RT_m --> t2
    RT_m --> wm2
    RT_m --> ar
```

**Pipeline steps** (what the maintainer prompt instructs):

1. **Scan** -- `scan()` plus optional reads of `index.md` and `summaries/`
2. **Merge duplicates** -- archive or edit redundant files
3. **Archive low-value** -- `archive()` moves files to `memory/archived/`
4. **Consolidate** -- combine topics via `edit()` / `write()`
5. **Re-index** -- agent uses `edit("index.md", ...)` to refresh the memory index

---

## Deployment model

Lerim runs as a **single process** (`lerim serve`) that provides the daemon loop and JSON API. The **web UI** is **[Lerim Cloud](https://lerim.dev)**. Typically this runs inside a Docker container via `lerim up`, but can also be started directly.

Service commands (`ask`, `sync`, `maintain`, `status`) are thin HTTP clients that forward requests to the server.

```
CLI / clients                       lerim serve (Docker or direct)
-----                               --------
lerim ask "q"   --HTTP POST-->      /api/ask
lerim sync      --HTTP POST-->      /api/sync
lerim maintain  --HTTP POST-->      /api/maintain
lerim status    --HTTP GET--->      /api/status
browser         --HTTPS------->     Lerim Cloud (web UI)

lerim init        (host only, no server needed)
lerim project add (host only, no server needed)
lerim up/down     (host only, manages Docker)
```

=== "Docker (recommended)"

    ```bash
    pip install lerim
    lerim init
    lerim project add .
    lerim up                    # starts container with daemon + JSON API
    ```

=== "Direct (development)"

    ```bash
    pip install lerim
    lerim init
    lerim connect auto
    lerim serve                 # daemon + JSON API in foreground
    ```

---

## Storage model

### Per-project: `<repo>/.lerim/`

```text
<repo>/.lerim/
├── memory/
│   ├── *.md                     # flat memory files (YAML frontmatter)
│   ├── index.md                 # optional index (maintained by the agent)
│   ├── summaries/YYYYMMDD/HHMMSS/  # episodic session summaries
│   └── archived/                # soft-deleted memories
└── workspace/                   # run artifacts (logs, per-run JSON)
```

### Global: `~/.lerim/`

```text
~/.lerim/
├── config.toml                  # user global configuration
├── index/sessions.sqlite3       # session catalog + job queue
├── cache/                       # compacted trace caches per platform
├── activity.log                 # append-only activity log
└── platforms.json               # platform detection cache
```

---

## Next steps

<div class="grid cards" markdown>

-   :material-brain:{ .lg .middle } **Memory Model**

    ---

    Learn about memory types and layout.

    [:octicons-arrow-right-24: Memory model](memory-model.md)

-   :material-robot:{ .lg .middle } **Supported Agents**

    ---

    See which coding agents Lerim can ingest sessions from.

    [:octicons-arrow-right-24: Supported agents](supported-agents.md)

-   :material-sync:{ .lg .middle } **Sync & Maintain**

    ---

    More on the sync and maintain pipelines.

    [:octicons-arrow-right-24: Sync & maintain](sync-maintain.md)

-   :material-cog:{ .lg .middle } **Configuration**

    ---

    TOML config, model roles, intervals, and tracing.

    [:octicons-arrow-right-24: Configuration](../configuration/overview.md)

</div>
