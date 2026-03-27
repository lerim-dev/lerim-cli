# Background sync and maintain

The **daemon loop** runs sync (hot path) and maintain (cold path) on independent
schedules. It is **not** a separate CLI command: it runs **inside** `lerim serve`
(and therefore inside `lerim up` / Docker).

## Intervals

Configure in `~/.lerim/config.toml` or `<repo>/.lerim/config.toml` under `[server]`:

| Setting | Typical default | Description |
|---------|-----------------|-------------|
| `sync_interval_minutes` | `10`–`30` (see shipped `default.toml`) | How often the daemon runs sync |
| `maintain_interval_minutes` | `60` | How often the daemon runs maintain |

Sessions are processed **sequentially in chronological order** (oldest first) so
later sessions can update memories from earlier ones.

## What to run

```bash
lerim serve          # JSON API + background loop (web UI: Lerim Cloud)
lerim up             # Docker: same combined process
```

Use `lerim sync` / `lerim maintain` for one-shot runs via the API, or rely on the
background loop when the server is up.

## Related

- [lerim serve](serve.md) — combined server entrypoint
- [lerim sync](sync.md) — one-shot hot path
- [lerim maintain](maintain.md) — one-shot cold path
- [lerim status](status.md) — queue and last-run info
