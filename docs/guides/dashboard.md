# Web UI (Lerim Cloud)

The interactive web UI for Lerim — sessions, memories, pipeline, and settings — is **not** bundled in `lerim-cli`. It lives in the **[lerim-cloud](https://github.com/lerim-dev/lerim-cloud)** repository and is deployed at **[lerim.dev](https://lerim.dev)**.

The `lerim` process still runs a **local JSON API** on port **8765** (default) when you use `lerim up` or `lerim serve`. The CLI (`lerim ask`, `lerim sync`, …) uses that API. Opening `http://localhost:8765/` without bundled static assets shows a short stub page with a link to Lerim Cloud.

```bash
lerim dashboard   # print local API URL + Cloud web UI hint
```

## Related

- [CLI: lerim serve](../cli/serve.md) — local API + daemon loop
- [CLI: lerim dashboard](../cli/dashboard.md) — print URLs
