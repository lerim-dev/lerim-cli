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
sync_interval_minutes = 30
maintain_interval_minutes = 60
sync_window_days = 7
sync_max_sessions = 50

[roles.agent]
provider = "minimax"
model = "MiniMax-M2.5"
api_base = ""
fallback_models = ["zai:glm-4.7"]
max_iters_sync = 30
max_iters_maintain = 50
max_iters_ask = 15
openrouter_provider_order = []
thinking = true
temperature = 1.0
max_tokens = 32000

[providers]
minimax = "https://api.minimax.io/v1"
zai = "https://api.z.ai/api/coding/paas/v4"
openai = "https://api.openai.com/v1"
openrouter = "https://openrouter.ai/api/v1"
opencode_go = "https://opencode.ai/zen/go/v1"
ollama = "http://127.0.0.1:11434"
mlx = "http://127.0.0.1:8000/v1"
auto_unload = true

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

**Agent** (`agent`) -- this is the **only** `dspy.LM` used by `LerimRuntime` for **sync, maintain, and ask** (DSPy ReAct). It must be configured for your provider.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `provider` | string | `"minimax"` | Provider backend (`minimax`, `zai`, `openrouter`, `openai`, `opencode_go`, `ollama`, ...). |
| `model` | string | `"MiniMax-M2.5"` | Model identifier. |
| `api_base` | string | `""` | Custom API base URL. Empty = use provider default from `[providers]`. |
| `fallback_models` | list | `["zai:glm-4.7"]` | Fallback model chain (format: `"model"` or `"provider:model"`). Auto-switch on quota/rate-limit errors. |
| `max_iters_sync` | int | `30` | Max ReAct iterations for sync (`ExtractAgent`). |
| `max_iters_maintain` | int | `50` | Max ReAct iterations for maintain (`MaintainAgent`). |
| `max_iters_ask` | int | `15` | Max ReAct iterations for ask (`AskAgent`). |
| `openrouter_provider_order` | list | `[]` | OpenRouter-specific provider ordering preference. |
| `thinking` | bool | `true` | Enable model thinking/reasoning. |
| `temperature` | float | `1.0` | Sampling temperature. MiniMax recommends 1.0 for creative output; 0.0 causes rigid pattern-copying. |
| `max_tokens` | int | `32000` | Max completion tokens. |

### `[providers]`

Default API base URLs per provider. Per-role `api_base` takes precedence over these.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `minimax` | string | `"https://api.minimax.io/v1"` | MiniMax API base (Coding Plan). |
| `zai` | string | `"https://api.z.ai/api/coding/paas/v4"` | Z.AI API base (Coding Plan). |
| `openai` | string | `"https://api.openai.com/v1"` | OpenAI API base. |
| `openrouter` | string | `"https://openrouter.ai/api/v1"` | OpenRouter API base. |
| `opencode_go` | string | `"https://opencode.ai/zen/go/v1"` | OpenCode Go API base. |
| `ollama` | string | `"http://127.0.0.1:11434"` | Ollama local API base. Use `http://host.docker.internal:11434` inside Docker. |
| `mlx` | string | `"http://127.0.0.1:8000/v1"` | vllm-mlx local API base (Apple Silicon). |
| `auto_unload` | bool | `true` | Unload Ollama models from RAM after each sync/maintain cycle. Set `false` to keep models loaded between cycles. |

### `[cloud]`

Lerim Cloud web UI / API endpoint (token via `lerim auth` or `LERIM_CLOUD_TOKEN`).

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `endpoint` | string | `https://api.lerim.dev` | Cloud API base URL. |

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
