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

[memory.decay]
enabled = true
decay_days = 180                    # days of no access before full decay
min_confidence_floor = 0.1          # decay never drops below this multiplier
archive_threshold = 0.2             # effective confidence below this → archive candidate
recent_access_grace_days = 30       # recently accessed memories skip archiving

[server]
host = "127.0.0.1"
port = 8765
sync_interval_minutes = 10          # sync hot path interval
maintain_interval_minutes = 60      # maintain cold path interval
sync_window_days = 7
sync_max_sessions = 50

[roles.lead]
provider = "minimax"                   # minimax | zai | openrouter | openai
model = "MiniMax-M2.5"
api_base = ""
fallback_models = ["zai:glm-4.7"]
timeout_seconds = 300
max_iterations = 10
openrouter_provider_order = []
thinking = true                        # enable model thinking/reasoning (Ollama Qwen 3.5)

[roles.extract]
provider = "minimax"
model = "MiniMax-M2.5"
api_base = ""
fallback_models = ["zai:glm-4.5-air"]
timeout_seconds = 180
max_window_tokens = 300000
window_overlap_tokens = 5000
openrouter_provider_order = []
thinking = true
max_workers = 4                        # parallel window processing (set 1 for local/Ollama models)

[roles.summarize]
provider = "minimax"
model = "MiniMax-M2.5"
api_base = ""
fallback_models = ["zai:glm-4.5-air"]
timeout_seconds = 180
max_window_tokens = 300000
window_overlap_tokens = 5000
openrouter_provider_order = []
thinking = true
max_workers = 4                        # parallel window processing (set 1 for local/Ollama models)

[providers]
# Default API base URLs per provider.
# Override here to point all roles using that provider at a different endpoint.
# Per-role api_base (under [roles.*]) takes precedence over these defaults.
minimax = "https://api.minimax.io/v1"
zai = "https://api.z.ai/api/coding/paas/v4"
openai = "https://api.openai.com/v1"
openrouter = "https://openrouter.ai/api/v1"
ollama = "http://127.0.0.1:11434"
# Docker: use "http://host.docker.internal:11434" if Ollama runs on the host.
litellm_proxy = "http://127.0.0.1:4000"
mlx = "http://127.0.0.1:8000/v1"
auto_unload = true                     # unload Ollama models after each sync/maintain cycle to free RAM

[tracing]
enabled = false                          # set true or LERIM_TRACING=1 to enable
include_httpx = false                    # capture raw HTTP request/response bodies
include_content = true                   # include prompt/completion text in spans

[agents]
# Map agent names to session directory paths.
# claude = "~/.claude/projects"
# codex = "~/.codex/sessions"
# cursor = "~/Library/Application Support/Cursor/User/globalStorage"
# opencode = "~/.local/share/opencode"

[projects]
# Map project short names to absolute host paths.
# my-project = "~/codes/my-project"
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

### `[memory.decay]`

Time-based memory decay and archiving policy. The maintain path uses these
settings to gradually reduce confidence of unaccessed memories and archive
low-value entries.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `enabled` | bool | `true` | Enable time-based confidence decay during maintain runs. |
| `decay_days` | int | `180` | Days of no access before a memory reaches full decay. |
| `min_confidence_floor` | float | `0.1` | Decay never drops the effective confidence multiplier below this value. |
| `archive_threshold` | float | `0.2` | Effective confidence below this value makes the memory an archive candidate. |
| `recent_access_grace_days` | int | `30` | Memories accessed within this many days skip archiving regardless of confidence. |

!!! tip "How decay works"
    Decay is applied during `lerim maintain`. A memory's effective confidence is:
    `original_confidence * decay_multiplier`. The multiplier decreases linearly from
    1.0 to `min_confidence_floor` over `decay_days` of no access. Memories below
    `archive_threshold` (and not recently accessed) are moved to `memory/archived/`.

### `[server]`

HTTP server and daemon loop configuration.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `host` | string | `"127.0.0.1"` | Bind address for the HTTP server. Use `0.0.0.0` for Docker. |
| `port` | int | `8765` | Port for the JSON API (`lerim serve`). |
| `sync_interval_minutes` | int | `10` | How often the daemon runs the sync hot path. |
| `maintain_interval_minutes` | int | `60` | How often the daemon runs the maintain cold path. |
| `sync_window_days` | int | `7` | Default time window for session discovery (can be overridden with `--window`). |
| `sync_max_sessions` | int | `50` | Max sessions to extract per sync run. |

### `[roles.*]` -- Model roles

Four roles control which LLM handles each task. See [Model Roles](model-roles.md)
for a full breakdown.

**Orchestration role** (`lead`) -- used by DSPy ReAct agent modules:

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `provider` | string | `"minimax"` | Provider backend: `minimax`, `zai`, `openrouter`, `openai`, `ollama`, `mlx`. |
| `model` | string | varies | Model identifier (e.g. `MiniMax-M2.5`). |
| `api_base` | string | `""` | Custom API base URL. Empty = use provider default from `[providers]`. |
| `fallback_models` | list | `[]` | Fallback model chain (format: `"model"` or `"provider:model"`). |
| `timeout_seconds` | int | `300`/`180` | Request timeout. |
| `max_iterations` | int | `10` | Max agent tool-call iterations. |
| `openrouter_provider_order` | list | `[]` | OpenRouter-specific provider ordering preference. |
| `thinking` | bool | `true` | Enable model thinking/reasoning. Set `false` for non-reasoning models. |

**Codex role** (`codex`) -- optional; parsed for Cloud / future use. **Not** consumed by `LerimRuntime` at runtime today.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `provider` | string | `opencode_go` | Provider backend for the codex role slot. |
| `model` | string | `minimax-m2.5` | Model identifier. |
| `api_base` | string | `""` | Custom API base. Empty = use default from `[providers]`. |
| `timeout_seconds` | int | `600` | Request timeout. |
| `idle_timeout_seconds` | int | `120` | Idle timeout (reserved). |

**DSPy roles** (`extract`, `summarize`) -- used by DSPy ChainOfThought pipelines:

All keys from orchestration roles (including `thinking`), plus:

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `max_window_tokens` | int | `300000` | Maximum tokens per transcript window for DSPy processing. |
| `window_overlap_tokens` | int | `5000` | Overlap between consecutive windows when splitting large transcripts. |
| `max_workers` | int | `4` | Parallel window processing threads. Set `1` for local/Ollama models to avoid RAM contention. |

### `[providers]`

Default API base URLs per provider. Per-role `api_base` takes precedence over these.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `minimax` | string | `"https://api.minimax.io/v1"` | MiniMax API base (Coding Plan). |
| `zai` | string | `"https://api.z.ai/api/coding/paas/v4"` | Z.AI API base (Coding Plan). |
| `openai` | string | `"https://api.openai.com/v1"` | OpenAI API base. |
| `openrouter` | string | `"https://openrouter.ai/api/v1"` | OpenRouter API base. |
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
