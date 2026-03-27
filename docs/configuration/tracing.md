# Tracing

Lerim uses OpenTelemetry for agent observability via
[Pydantic Logfire](https://logfire.pydantic.dev). Stderr logs are kept minimal --
detailed traces (model calls, tool calls, tokens, timing) go through OTel spans.

## What gets traced

When tracing is enabled, Logfire records spans for work instrumented at startup (see **What happens at startup** below). The lead runtime uses the OpenAI Agents SDK (`Runner.run`); **built-in OpenAI Agents SDK tracing is disabled** in `LerimOAIAgent` so traces are not exported to OpenAI’s hosted tracing by default.

Typical visibility:

- DSPy LLM calls (via `logfire.instrument_dspy()`)
- Optional raw HTTP when `include_httpx = true` (provider debugging)
- Per-run LLM cost from cost tracking (where the provider exposes usage)

Each sync/maintain run also writes `agent_trace.json` under the run workspace for a full tool/message history (not Logfire-specific).

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

- **Timeline** -- DSPy and HTTP-related activity as spans (lead SDK tracing disabled; use `agent_trace.json` in run folders for full tool turns)
- **Span tree** -- nested spans from DSPy and optional httpx
- **Token usage** -- per-span token counts
- **Timing** -- latency for each operation

!!! tip "DSPy visibility"
    DSPy pipelines run with `verbose=False` in stderr, but their LLM calls
    are visible in Logfire via httpx spans when `include_httpx = true`.
