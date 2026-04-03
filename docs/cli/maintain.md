# lerim maintain

Refine existing memories offline (cold path).

## Overview

Cold-path: offline memory refinement. Scans existing memories and merges duplicates, archives low-value items, and consolidates related memories. Archived items go to `memory/archived/`. Requires a running server (`lerim up` or `lerim serve`).

## Syntax

```bash
lerim maintain [--force] [--dry-run]
```

## Parameters

<div class="param-field">
  <div class="param-header">
    <span class="param-name">--force</span>
    <span class="param-type">boolean</span>
    <span class="param-badge default">default: off</span>
  </div>
  <p class="param-desc">Force maintenance even if a recent run was completed.</p>
</div>

<div class="param-field">
  <div class="param-header">
    <span class="param-name">--dry-run</span>
    <span class="param-type">boolean</span>
    <span class="param-badge default">default: off</span>
  </div>
  <p class="param-desc">Record a run but skip actual memory changes.</p>
</div>

## Examples

```bash
lerim maintain                # run one maintenance pass
lerim maintain --force        # force maintenance even if recently run
lerim maintain --dry-run      # preview only, no writes
```

## What maintenance does

1. **Scan** — Inspect memories (manifest + files under `memory/`)
2. **Merge duplicates** — Consolidate overlapping markdown files
3. **Archive low-value** — Move unneeded files to `memory/archived/`
4. **Consolidate** — Strengthen related entries via edits or new files
5. **Re-index** — Refresh `MEMORY.md` when instructed by the agent

!!! info "Non-destructive"
    Maintenance is non-destructive — archived memories are moved to `memory/archived/` rather than deleted.

## Related commands

<div class="grid cards" markdown>

-   :material-sync: **lerim sync**

    ---

    Index and extract new memories

    [:octicons-arrow-right-24: lerim sync](sync.md)

-   :material-refresh: **Background loop**

    ---

    Sync + maintain intervals (inside `lerim serve`)

    [:octicons-arrow-right-24: Background loop](daemon.md)

</div>
