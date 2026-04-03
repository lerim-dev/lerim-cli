# config.toml Reference

Complete reference for every configuration key in Lerim's TOML config.

## Full default config

This is the complete `src/lerim/config/default.toml` shipped with the package.
Your `~/.lerim/config.toml` and `<repo>/.lerim/config.toml` override any of these
values.

```toml
# Lerim default configuration — shipped with the package.
# Override in ~/.lerim/config.toml (user) or <repo>/.lerim/config.toml (project).
# API keys come from environment variables only.

[data]
dir = "~/.lerim"

[memory]
scope = "project_fallback_global"   # project_fallback_global | project_only | global_only
project_dir_name = ".lerim"

[server]
host = "127.0.0.1"
port = 8765
sync_interval_minutes = 30          # sync hot path interval
maintain_interval_minutes = 60       # maintain cold path interval
sync_window_days = 7
sync_max_sessions = 50
parallel_pipelines = true               # run extract pipelines in parallel (set false for local/Ollama models)

[roles.lead]
provider = "opencode_go"               # opencode_go | minimax | zai | openrouter | openai | ollama
model = "minimax-m2.5"                 # OpenCode Go models: minimax-m2.7, minimax-m2.5, kimi-k2.5, glm-5
api_base = ""
# Model names are auto-normalized per provider (e.g. minimax-m2.5 → MiniMax-M2.5 for minimax provider).
fallback_models = ["minimax:MiniMax-M2.5"]  # auto-switch on quota/rate-limit errors
timeout_seconds = 600
max_iterations = 30
max_iters_sync = 50                    # max ReAct iterations for lead agent in sync flow
max_iters_maintain = 100               # max ReAct iterations for lead agent in maintain flow
max_iters_ask = 30                     # max ReAct iterations for lead agent in ask flow
openrouter_provider_order = []
thinking = true
max_tokens = 32000

[roles.extract]
provider = "opencode_go"
model = "minimax-m2.5"                 # DSPy extraction model
api_base = ""
fallback_models = []
timeout_seconds = 300
max_window_tokens = 100000
window_overlap_tokens = 5000
openrouter_provider_order = []
thinking = true
max_tokens = 32000
max_workers = 4

[providers]
# Default API base URLs per provider.
# Override here to point all roles using that provider at a different endpoint.
# Per-role api_base (under [roles.*]) takes precedence over these defaults.
minimax = "https://api.minimax.io/v1"
zai = "https://api.z.ai/api/coding/paas/v4"
openai = "https://api.openai.com/v1"
openrouter = "https://openrouter.ai/api/v1"
opencode_go = "https://opencode.ai/zen/go/v1"
ollama = "http://127.0.0.1:11434"
# Docker: use "http://host.docker.internal:11434" if Ollama runs on the host.
litellm_proxy = "http://127.0.0.1:4000"
mlx = "http://127.0.0.1:8000/v1"
auto_unload = true                     # unload Ollama models after each sync/maintain cycle to free RAM

[tracing]
enabled = false                          # set true or LERIM_TRACING=1 to enable
include_httpx = false                    # capture raw HTTP request/response bodies
include_content = true                   # include prompt/completion text in spans

[cloud]
endpoint = "https://api.lerim.dev"
# token is set via `lerim auth` or LERIM_CLOUD_TOKEN env var

[agents]
# Map agent names to session directory paths.
# claude = "~/.claude/projects"
# codex = "~/.codex/sessions"
# cursor = "~/Library/Application Support/Cursor/User/globalStorage"
# opencode = "~/.local/share/opencode"

[projects]
# Map project short names to absolute host paths.
# my-project = "~/codes/my-project"

# --- Planned features (uncomment when implemented) ---
# [embeddings]
# ...
```

---

## Section reference

### `[data]`

Global data directory for Lerim's shared state.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `dir` | string | `"~/.lerim"` | Root directory for global config, session DB, caches, and activity log. Tilde is expanded at runtime. |

### `[memory]`

Memory scope and project directory naming.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `scope` | string | `"project_fallback_global"` | Memory scope mode. One of: `project_fallback_global`, `project_only`, `global_only`. |
| `project_dir_name` | string | `".lerim"` | Name of the per-project directory created inside each repository root. |

**Scope modes:**

| Mode | Behavior |
|------|----------|
| `project_fallback_global` | Memories stored per-project. Global fallback is configured but not yet implemented in runtime. |
| `project_only` | Memories stored per-project only. |
| `global_only` | Memories stored in `~/.lerim/memory/` (for use outside git repos). |

### Memory decay (roadmap)

Time-based decay and numeric **confidence** scores are **not** implemented in the current runtime — there is no `[memory.decay]` section in the shipped `default.toml`. The maintain agent archives or merges memories using prompts and tools only.

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

### `[roles.*]` -- Model roles

See [Model Roles](model-roles.md) for detail. Shipped defaults define **`[roles.lead]`** and **`[roles.extract]`**.

**Lead** (`lead`) -- this is the **only** `dspy.LM` used by `LerimRuntime` for **sync, maintain, and ask** (DSPy ReAct). It must be configured for your provider.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `provider` | string | `opencode_go` | Provider backend (`opencode_go`, `minimax`, `zai`, `openrouter`, `openai`, `ollama`, …). |
| `model` | string | varies | Model identifier. |
| `api_base` | string | `""` | Custom API base URL. Empty = use provider default from `[providers]`. |
| `fallback_models` | list | `[]` | Fallback model chain (format: `"model"` or `"provider:model"`). |
| `timeout_seconds` | int | `600` | Request timeout. |
| `max_iterations` | int | `30` | Max ReAct iterations (generic cap). |
| `max_iters_sync` | int | `50` | Max iterations for sync (`ExtractAgent`). |
| `max_iters_maintain` | int | `100` | Max iterations for maintain (`MaintainAgent`). |
| `max_iters_ask` | int | `30` | Max iterations for ask (`AskAgent`). |
| `max_tokens` | int | `32000` | Max completion tokens. |
| `openrouter_provider_order` | list | `[]` | OpenRouter-specific provider ordering preference. |
| `thinking` | bool | `true` | Enable model thinking/reasoning. |

**Extract** (`extract`) -- loaded into `Config` for API / tooling (e.g. windowing parameters). **The ReAct flows do not switch to a separate LM**; runtime uses `lead` for `ExtractAgent`.

| Key | Type | Description |
|-----|------|-------------|
| `max_window_tokens` | int | Reserved for windowed processing / future use. |
| `window_overlap_tokens` | int | Overlap between windows. |
| `max_workers` | int | Parallel workers for batch extraction when used. |

**Codex** (`codex`) -- optional block parsed for Cloud / future use; **not** consumed by `LerimRuntime` today.

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
| `litellm_proxy` | string | `"http://127.0.0.1:4000"` | LiteLLM proxy base (used for Ollama think-off routing). |
| `mlx` | string | `"http://127.0.0.1:8000/v1"` | vllm-mlx local API base (Apple Silicon). |
| `auto_unload` | bool | `true` | Unload Ollama models from RAM after each sync/maintain cycle. Set `false` to keep models loaded between cycles. |

### `[tracing]`

OpenTelemetry tracing via Logfire. See [Tracing](tracing.md) for setup instructions.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `enabled` | bool | `false` | Enable tracing. Also toggleable via `LERIM_TRACING=1` env var. |
| `include_httpx` | bool | `false` | Capture raw HTTP request/response bodies in spans. |
| `include_content` | bool | `true` | Include prompt and completion text in spans. |

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
