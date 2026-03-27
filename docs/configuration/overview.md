# Configuration Overview

Lerim uses a layered TOML configuration system. Every setting has a sensible default
shipped with the package -- you only need to override what you want to change.

## Config layers

Settings are resolved in priority order (highest wins):

| Priority | Source | Purpose |
|----------|--------|---------|
| 4 (highest) | `LERIM_CONFIG` env var | Explicit override (CI, tests) |
| 3 | `<repo>/.lerim/config.toml` | Per-project overrides |
| 2 | `~/.lerim/config.toml` | User global settings |
| 1 (lowest) | `src/lerim/config/default.toml` | Package defaults |

Each layer is deep-merged into the previous one. A key set in a higher layer
replaces the same key from a lower layer; keys not present in the higher layer
are inherited from below.

!!! info "Created automatically"
    `lerim init` writes `~/.lerim/config.toml` with your detected agents and
    initial settings. `lerim project add .` appends a project entry to the same
    file. You can also edit the file directly.

## API keys

API keys are **never** stored in TOML files. They come from environment variables only:

| Variable | Provider | Required when |
|----------|----------|---------------|
| `OPENCODE_API_KEY` | OpenCode Go / Zen | When any role uses `provider = "opencode_go"` (common in shipped defaults) |
| `MINIMAX_API_KEY` | MiniMax | When any role uses `provider = "minimax"` |
| `ZAI_API_KEY` | Z.AI | When any role uses `provider = "zai"` |
| `OPENROUTER_API_KEY` | OpenRouter | When any role uses `provider = "openrouter"` |
| `OPENAI_API_KEY` | OpenAI | When any role uses `provider = "openai"` |

!!! info "Only set what you use"
    You only need API keys for the providers referenced in your `[roles.*]` config. Switch providers freely — just set the matching key.

!!! warning "No fallback"
    If a required API key is missing, Lerim raises an error immediately.
    There is no silent fallback behavior.

## Config sections at a glance

=== "Agents"

    Maps agent platform names to session directory paths. Written by `lerim connect`
    or `lerim init`.

    ```toml
    [agents]
    claude = "~/.claude/projects"
    codex = "~/.codex/sessions"
    ```

=== "Projects"

    Maps project short names to absolute host paths. Written by `lerim project add`.

    ```toml
    [projects]
    lerim-cli = "~/codes/personal/lerim/lerim-cli"
    my-app = "~/codes/my-app"
    ```

=== "Model Roles"

    Four roles control which models handle each task. See
    [Model Roles](model-roles.md) for details.

    ```toml
    [roles.lead]
    provider = "minimax"
    model = "MiniMax-M2.5"
    ```

=== "Server"

    Host, port, and daemon intervals.

    ```toml
    [server]
    host = "127.0.0.1"
    port = 8765
    sync_interval_minutes = 10
    ```

## Sub-pages

<div class="grid cards" markdown>

-   :material-file-document-outline: **Full config.toml Reference**

    ---

    Every section, key, and default value explained.

    [:octicons-arrow-right-24: config.toml Reference](config-toml.md)

-   :material-brain: **Model Roles**

    ---

    Configure which models handle lead, extract, summarize, and optional codex (config surface) tasks.

    [:octicons-arrow-right-24: Model Roles](model-roles.md)

-   :material-chart-timeline-variant: **Tracing**

    ---

    OpenTelemetry setup with Logfire for agent observability.

    [:octicons-arrow-right-24: Tracing](tracing.md)

</div>
