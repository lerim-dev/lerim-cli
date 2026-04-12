# config.toml Reference

Complete reference for every configuration key in Lerim's TOML config.

## Full default config

This is the complete `src/lerim/config/default.toml` shipped with the package.
Your `~/.lerim/config.toml` overrides any of these values.

```toml
# Lerim default configuration — shipped with the package.
# Override in ~/.lerim/config.toml (user)
# API keys come from environment variables only in ~/.lerim/.env

[data]
dir = "~/.lerim"

[server]
host = "127.0.0.1"
port = 8765
sync_interval_minutes = 30          # sync hot path interval
maintain_interval_minutes = 60       # maintain cold path interval
sync_window_days = 7
sync_max_sessions = 50

[roles.agent]
provider = "minimax"               # opencode_go | minimax | zai | openrouter | openai | ollama
model = "MiniMax-M2.7"                 # package default model for minimax provider
api_base = ""
# Model names are auto-normalized per provider (e.g. minimax-m2.5 → MiniMax-M2.5 for minimax provider).
fallback_models = []  # disabled for now — ensure exact model is used, no silent fallback
# PydanticAI single-pass sync auto-scales its request budget from trace
# size via lerim.agents.tools.compute_request_budget(trace_path). Small
# traces get the 50-turn floor; 2000-line traces get ~65; pathological
# inputs clamp at 100. The formula lives in tools.py and is the single
# source of truth — no per-pass limits in config.
max_iters_maintain = 50                # max request turns for maintain flow
max_iters_ask = 20                     # max request turns for ask flow
openrouter_provider_order = []
thinking = true
top_p = 0.95
top_k = 40
temperature = 1.0
max_tokens = 32000
parallel_tool_calls = true

[providers]
# Default API base URLs per provider.
# Override here to point all roles using that provider at a different endpoint.
# Per-role api_base (under [roles.*]) takes precedence over these defaults.
minimax = "https://api.minimax.io/v1"
minimax_anthropic = "https://api.minimax.io/anthropic"
zai = "https://api.z.ai/api/coding/paas/v4"
openai = "https://api.openai.com/v1"
openrouter = "https://openrouter.ai/api/v1"
opencode_go = "https://opencode.ai/zen/go/v1"
ollama = "http://127.0.0.1:11434"
# Docker: use "http://host.docker.internal:11434" if Ollama runs on the host.
mlx = "http://127.0.0.1:8000/v1"
auto_unload = true                     # unload Ollama models after each sync/maintain cycle to free RAM

[cloud]
endpoint = "https://api.lerim.dev"

[agents]
# claude = "~/.claude/projects"
# codex = "~/.codex/sessions"
# cursor = "~/Library/Application Support/Cursor/User/globalStorage"
# opencode = "~/.local/share/opencode"

[projects]
# my-project = "~/codes/my-project"
```

---

## Section reference

### `[data]`

Global data directory for Lerim's shared state.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `dir` | string | `"~/.lerim"` | Root directory for global config, session DB, caches, and activity log. Tilde is expanded at runtime. |

### `[server]`

HTTP server and daemon loop configuration.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `host` | string | `"127.0.0.1"` | Bind address for the HTTP server. Use `0.0.0.0` for Docker. |
| `port` | int | `8765` | Port for the JSON API (`lerim serve`). |
| `sync_interval_minutes` | int | `30` | How often the daemon runs the sync hot path. |
| `maintain_interval_minutes` | int | `60` | How often the daemon runs the maintain cold path. |
| `sync_window_days` | int | `7` | Default time window for session discovery (can be overridden with `--window`). |
| `sync_max_sessions` | int | `50` | Max sessions to extract per sync run. |

### `[roles.agent]` -- Model role

See [Model Roles](model-roles.md) for detail. Shipped defaults define a single **`[roles.agent]`** role.

**Agent** (`agent`) -- this is the **only** PydanticAI model role used by `LerimRuntime` for **sync, maintain, and ask**.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `provider` | string | `"minimax"` | Provider backend (`minimax`, `zai`, `openrouter`, `openai`, `opencode_go`, `ollama`, ...). |
| `model` | string | `"MiniMax-M2.7"` | Model identifier. |
| `api_base` | string | `""` | Custom API base URL. Empty = use provider default from `[providers]`. |
| `fallback_models` | list | `[]` | Optional ordered fallback model chain (format: `"model"` or `"provider:model"`). Shipped default disables fallback (`[]`). |
| `max_iters_maintain` | int | `50` | Request-turn budget for maintain flow (`run_maintain`). |
| `max_iters_ask` | int | `20` | Request-turn budget for ask flow (`run_ask`). |
| `openrouter_provider_order` | list | `[]` | OpenRouter-specific provider ordering preference. |
| `thinking` | bool | `true` | Enable model thinking/reasoning. |
| `temperature` | float | `1.0` | Sampling temperature. |
| `top_p` | float | `0.95` | Nucleus sampling control when supported. |
| `top_k` | int | `40` | Top-k sampling control (sent via `extra_body`). |
| `max_tokens` | int | `32000` | Max completion tokens. |
| `parallel_tool_calls` | bool | `true` | Enable parallel tool calls when provider/model supports it. |

!!! info "Sync budget"
    Sync extraction does not use a static `max_iters_sync` key. The extraction request budget is auto-scaled from trace size at run time.

### `[providers]`

Default API base URLs per provider. Per-role `api_base` takes precedence over these.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `minimax` | string | `"https://api.minimax.io/v1"` | MiniMax OpenAI-compatible API base. |
| `minimax_anthropic` | string | `"https://api.minimax.io/anthropic"` | MiniMax Anthropic-compatible API base used by the runtime. |
| `zai` | string | `"https://api.z.ai/api/coding/paas/v4"` | Z.AI API base (Coding Plan). |
| `openai` | string | `"https://api.openai.com/v1"` | OpenAI API base. |
| `openrouter` | string | `"https://openrouter.ai/api/v1"` | OpenRouter API base. |
| `opencode_go` | string | `"https://opencode.ai/zen/go/v1"` | OpenCode Go API base. |
| `ollama` | string | `"http://127.0.0.1:11434"` | Ollama local API base. Use `http://host.docker.internal:11434` inside Docker. |
| `mlx` | string | `"http://127.0.0.1:8000/v1"` | vllm-mlx local API base (Apple Silicon). |
| `auto_unload` | bool | `true` | Unload Ollama models from RAM after each sync/maintain cycle. Set `false` to keep models loaded between cycles. |

### `[cloud]`

Hosted service API endpoint (token via `lerim auth` or `LERIM_CLOUD_TOKEN`).

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `endpoint` | string | `https://api.lerim.dev` | Hosted API base URL. |

### `[agents]`

Maps agent platform names to their session directory paths. Managed by
`lerim connect` and `lerim init`.

```toml
[agents]
claude = "~/.claude/projects"
codex = "~/.codex/sessions"
cursor = "~/Library/Application Support/Cursor/User/globalStorage"
opencode = "~/.local/share/opencode"
```

!!! info "Auto-managed"
    You can edit this section directly, but `lerim connect auto` and `lerim init`
    will overwrite it with detected platform paths.

### `[projects]`

Maps project short names to absolute host paths. Managed by `lerim project add`.

```toml
[projects]
lerim-cli = "~/codes/personal/lerim/lerim-cli"
my-app = "~/codes/my-app"
```

Each registered project gets a `.lerim/` directory for per-project memory storage.
