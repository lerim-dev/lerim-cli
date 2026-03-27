# lerim dashboard

Print the **local API** base URL and point to **Lerim Cloud** for the web UI.

## Overview

The browser UI is hosted separately (**[lerim.dev](https://lerim.dev)**). This command does not start a server — it only prints where the JSON API lives when `lerim serve` or `lerim up` is running.

## Syntax

```bash
lerim dashboard [--port PORT]
```

## Examples

```bash
lerim dashboard
```

Sample output:

```
API (lerim serve): http://localhost:8765/
Web UI: https://lerim.dev — open Lerim Cloud while this server runs.
```

## Parameters

| Flag | Description |
|------|-------------|
| `--port` | Port to show in the API URL (defaults to config `server.port`, usually `8765`) |

## See also

- [lerim serve](serve.md) — HTTP API + daemon loop
- [Web UI (Lerim Cloud)](../guides/dashboard.md)
