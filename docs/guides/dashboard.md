# Dashboard

The Lerim dashboard is a local web UI for session analytics, memory browsing,
pipeline status, and runtime configuration.

![Dashboard](../assets/dashboard.png)

## Launching the dashboard

=== "Docker (recommended)"

    The dashboard is automatically served when Lerim is running via Docker:

    ```bash
    lerim up
    ```

    Open `http://localhost:8765` in your browser.

=== "Standalone"

    When running without Docker, the dashboard is served by `lerim serve`:

    ```bash
    lerim serve
    ```

    Open `http://localhost:8765`. To use a custom host/port:

    ```bash
    lerim serve --host 0.0.0.0 --port 9000
    ```

To print the dashboard URL at any time:

```bash
lerim dashboard
```

## Tabs

### Overview

High-level metrics and charts for your project:

- Total sessions, messages, tool calls, errors
- Token usage breakdown
- Activity heatmap by day and hour
- Model usage distribution

!!! info "Filters"
    Top bar filters (**Agent** and **Scope**) update all dashboard metrics and
    run listings.

### Runs

Searchable session list (50 per page) with:

- Session status and metadata (agent, repo, timestamp, duration)
- Message and tool call counts
- Token usage per session
- Click any run to open a full-screen chat viewer

### Memories

Library and editor for memory records:

- Filter by primitive type (decisions, learnings)
- Inspect memory details (frontmatter, body, tags)
- Edit title, body, kind, confidence, and tags directly in the UI
- View memory creation and update history

### Pipeline

Real-time pipeline status:

- Sync and maintain run history
- Extraction queue state (pending, in-progress, completed)
- Latest extraction report with candidate counts

### Settings

Dashboard-editable configuration:

- Server settings (host, port, intervals)
- Model role configuration (provider, model per role)
- Tracing toggle and options

Changes save directly to `~/.lerim/config.toml`.

!!! warning "Restart may be required"
    Some setting changes (like model roles) take effect on the next
    sync/maintain cycle. Server settings require a restart.

## HTTP API

The dashboard is backed by a JSON API that also serves as the interface for the
thin CLI, skills, and external agents. Key endpoints:

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `GET` | `/api/health` | Health check |
| `GET` | `/api/status` | Runtime state |
| `POST` | `/api/ask` | Query memories |
| `POST` | `/api/sync` | Trigger sync |
| `POST` | `/api/maintain` | Trigger maintenance |
| `GET` | `/api/memories` | List memories |
| `GET` | `/api/search` | Search memories |

The full API is served by `lerim serve` (or `lerim up` via Docker) on `http://localhost:8765`.
