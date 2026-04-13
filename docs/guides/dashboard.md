# Dashboard (Coming Soon)

The dashboard UI is not released yet.

For now, use the CLI and local API directly:

```bash
lerim status
lerim ask "What changed?"
lerim sync
lerim maintain
```

The local API is available on port `8765` (default) when you run `lerim up` or `lerim serve`.

```bash
curl http://localhost:8765/api/health
```

## Related

- [CLI: lerim serve](../cli/serve.md) — local API + daemon loop
- [CLI: lerim dashboard](../cli/dashboard.md) — prints CLI alternatives
- [CLI: lerim status](../cli/status.md) — runtime overview
