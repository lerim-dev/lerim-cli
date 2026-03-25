# Tracing

Lerim uses OpenTelemetry for agent observability via
[Pydantic Logfire](https://logfire.pydantic.dev). Stderr logs are kept minimal --
detailed traces (model calls, tool calls, tokens, timing) go through OTel spans.

## What gets traced

When tracing is enabled, each `agent.run_sync()` emits a trace with spans for:

- Model requests (prompt, completion, token counts)
- Tool calls (name, input, output, duration)
- DSPy pipeline steps (extraction, summarization)
- Per-run LLM cost (via OpenRouter's `usage.cost` response field)

## One-time setup

### 1. Install Logfire

```bash
pip install logfire
```

### 2. Authenticate

```bash
logfire auth
```

This opens a browser to link your Logfire account and stores a token in
`~/.logfire/`.

### 3. Create a project

```bash
logfire projects new
```

Choose a project name (e.g. `lerim`). This is where your traces will appear
in the Logfire dashboard.

!!! info "Free tier"
    Logfire has a free tier that is sufficient for development and personal use.
    View your traces at [logfire.pydantic.dev](https://logfire.pydantic.dev).

## Enable tracing

=== "Environment variable"

    Quick toggle for a single command:

    ```bash
    LERIM_TRACING=1 lerim sync
    LERIM_TRACING=1 lerim ask "Why did we choose Postgres?"
    ```

=== "Config file"

    Persistent toggle in `~/.lerim/config.toml` or `<repo>/.lerim/config.toml`:

    ```toml
    [tracing]
    enabled = true
    ```

## Configuration options

All options live under the `[tracing]` section in TOML config:

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `enabled` | bool | `false` | Enable OpenTelemetry tracing. Also toggleable via `LERIM_TRACING=1` env var. |
| `include_httpx` | bool | `false` | Capture raw HTTP request/response bodies in spans. Useful for debugging provider issues. |
| `include_content` | bool | `true` | Include prompt and completion text in trace spans. Disable to reduce trace size. |

```toml
[tracing]
enabled = true
include_httpx = false
include_content = true
```

!!! warning "Sensitive data"
    When `include_content = true` (the default), prompt and completion text is
    sent to Logfire. If your transcripts contain sensitive information, consider
    setting `include_content = false`.

## What happens at startup

When tracing is enabled, Lerim calls `configure_tracing()` once before any agent
is constructed. This:

1. Configures Logfire with `service_name="lerim"` and `send_to_logfire="if-token-present"`
2. Instruments DSPy pipelines (`logfire.instrument_dspy()`)
3. Optionally instruments httpx (`logfire.instrument_httpx()`) if `include_httpx = true`

## Viewing traces

Open [logfire.pydantic.dev](https://logfire.pydantic.dev) and select your project.
You'll see:

- **Timeline** -- each sync/maintain/ask run as a top-level span
- **Span tree** -- nested model calls, tool invocations, and pipeline steps
- **Token usage** -- per-span token counts
- **Timing** -- latency for each operation

!!! tip "DSPy visibility"
    DSPy pipelines run with `verbose=False` in stderr, but their LLM calls
    are visible in Logfire via httpx spans when `include_httpx = true`.
