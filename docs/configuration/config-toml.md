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
scope = "project_fallback_global"
project_dir_name = ".lerim"

[memory.decay]
enabled = true
decay_days = 180
min_confidence_floor = 0.1
archive_threshold = 0.2
recent_access_grace_days = 30

[server]
host = "127.0.0.1"
port = 8765
sync_interval_minutes = 10
maintain_interval_minutes = 60
sync_window_days = 7
sync_max_sessions = 50

[roles.lead]
provider = "minimax"
model = "MiniMax-M2.5"
api_base = ""
fallback_models = ["zai:glm-4.7"]
timeout_seconds = 300
max_iterations = 10
openrouter_provider_order = []

[roles.explorer]
provider = "minimax"
model = "MiniMax-M2.5"
api_base = ""
fallback_models = ["zai:glm-4.7"]
timeout_seconds = 180
max_iterations = 8
openrouter_provider_order = []

[roles.extract]
provider = "minimax"
model = "MiniMax-M2.5"
api_base = ""
fallback_models = ["zai:glm-4.5-air"]
timeout_seconds = 180
max_window_tokens = 300000
window_overlap_tokens = 5000
openrouter_provider_order = []

[roles.summarize]
provider = "minimax"
model = "MiniMax-M2.5"
api_base = ""
fallback_models = ["zai:glm-4.5-air"]
timeout_seconds = 180
max_window_tokens = 300000
window_overlap_tokens = 5000
openrouter_provider_order = []

[providers]
minimax = "https://api.minimax.io/v1"
zai = "https://api.z.ai/api/coding/paas/v4"
openai = "https://api.openai.com/v1"
openrouter = "https://openrouter.ai/api/v1"
ollama = "http://127.0.0.1:11434"
mlx = "http://127.0.0.1:8000/v1"

[tracing]
enabled = false
include_httpx = false
include_content = true

[agents]
# claude = "~/.claude/projects"
# codex = "~/.codex/sessions"

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
| `port` | int | `8765` | Port for the HTTP server and dashboard. |
| `sync_interval_minutes` | int | `10` | How often the daemon runs the sync hot path. |
| `maintain_interval_minutes` | int | `60` | How often the daemon runs the maintain cold path. |
| `sync_window_days` | int | `7` | Default time window for session discovery (can be overridden with `--window`). |
| `sync_max_sessions` | int | `50` | Max sessions to extract per sync run. |

### `[roles.*]` -- Model roles

Four roles control which LLM handles each task. See [Model Roles](model-roles.md)
for a full breakdown.

**Orchestration roles** (`lead`, `explorer`) -- used by PydanticAI agents:

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `provider` | string | `"minimax"` | Provider backend: `minimax`, `zai`, `openrouter`, `openai`, `ollama`, `mlx`. |
| `model` | string | varies | Model identifier (e.g. `MiniMax-M2.5`). |
| `api_base` | string | `""` | Custom API base URL. Empty = use provider default from `[providers]`. |
| `fallback_models` | list | `[]` | Fallback model chain (format: `"model"` or `"provider:model"`). |
| `timeout_seconds` | int | `300`/`180` | Request timeout. |
| `max_iterations` | int | `10`/`8` | Max agent tool-call iterations. |
| `openrouter_provider_order` | list | `[]` | OpenRouter-specific provider ordering preference. |

**DSPy roles** (`extract`, `summarize`) -- used by DSPy ChainOfThought pipelines:

All keys from orchestration roles, plus:

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `max_window_tokens` | int | `300000` | Maximum tokens per transcript window for DSPy processing. |
| `window_overlap_tokens` | int | `5000` | Overlap between consecutive windows when splitting large transcripts. |

### `[providers]`

Default API base URLs per provider. Per-role `api_base` takes precedence over these.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `minimax` | string | `"https://api.minimax.io/v1"` | MiniMax API base (Coding Plan). |
| `zai` | string | `"https://api.z.ai/api/coding/paas/v4"` | Z.AI API base (Coding Plan). |
| `openai` | string | `"https://api.openai.com/v1"` | OpenAI API base. |
| `openrouter` | string | `"https://openrouter.ai/api/v1"` | OpenRouter API base. |
| `ollama` | string | `"http://127.0.0.1:11434"` | Ollama local API base. |
| `mlx` | string | `"http://127.0.0.1:8000"` | vllm-mlx local API base (Apple Silicon). |

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
