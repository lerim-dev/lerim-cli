# Dashboard

Lerim includes a local web dashboard for session analytics, memory browsing, and runtime status.

<p align="center">
  <img src="assets/dashboard.png" alt="Lerim dashboard" width="1100">
</p>

## Launch

When running via Docker (recommended), the dashboard is automatically available:

```bash
lerim up
# Dashboard at http://localhost:8765
```

Or run standalone without Docker:

```bash
lerim dashboard
# Then open http://127.0.0.1:8765
```

Custom host/port:

```bash
lerim dashboard --host 0.0.0.0 --port 9000
```

## Tabs

### Overview

High-level metrics and charts:

- Sessions processed, messages exchanged, tools called, errors encountered
- Token usage breakdown
- Activity by day and hour
- Model usage distribution

### Runs

Searchable session list (50 per page) with status and metadata. Click any run to open a full-screen chat viewer showing the complete conversation.

### Memories

Library and editor for memory records:

- Filter by type (decision/learning), tags, confidence
- Inspect individual memories
- Edit title, body, kind, confidence, and tags directly

### Pipeline

Sync and maintain status:

- Extraction queue state
- Latest extraction report
- Recent sync/maintain run timestamps

### Settings

Dashboard-editable configuration:

- Server settings (poll interval, sync window, max sessions)
- Model role configuration
- Tracing toggle

Changes are saved to `~/.lerim/config.toml`.

## HTTP API

The dashboard server also exposes a JSON API used by the thin CLI and skills.
Key endpoints include `/api/health`, `/api/ask`, `/api/sync`, `/api/maintain`,
`/api/memories`, `/api/search`, and `/api/status`. See [Architecture](architecture.md)
for the full endpoint list.

## Notes

- Top bar filters (`Agent`, `Scope`) update dashboard metrics and run listings across all tabs.
- The dashboard is read-only for memory content by default — edits go through the edit interface in the Memories tab.
- When running via Docker, the dashboard is served by the `lerim serve` process alongside the daemon loop and HTTP API.
