# Configuration

Lerim uses a layered TOML configuration system. API keys come from environment variables only.

## Config precedence

Lower entries override higher ones:

1. `src/lerim/config/default.toml` — shipped with the package (all defaults)
2. `~/.lerim/config.toml` — user global overrides
3. `<repo>/.lerim/config.toml` — project-specific overrides
4. `LERIM_CONFIG` env var — explicit path override (for CI/tests)

## API keys

API keys are set as environment variables, never in config files:

```bash
export OPENROUTER_API_KEY="sk-or-..."   # default provider
export OPENAI_API_KEY="sk-..."          # OpenAI provider
export ZAI_API_KEY="..."                # ZAI provider
export ANTHROPIC_API_KEY="..."          # Anthropic provider (optional)
```

## Default configuration

The full default config shipped with the package:

```toml
[data]
dir = "~/.lerim"

[memory]
scope = "project_fallback_global"   # project_fallback_global | project_only | global_only
project_dir_name = ".lerim"

[memory.decay]
enabled = true
decay_days = 180                    # days of no access before full decay
min_confidence_floor = 0.1          # decay never drops below this multiplier
archive_threshold = 0.2             # effective confidence below this -> archive candidate
recent_access_grace_days = 30       # recently accessed memories skip archiving

[server]
host = "127.0.0.1"
port = 8765
poll_interval_minutes = 30
sync_window_days = 7
sync_max_sessions = 50
sync_max_workers = 4
```

## Model roles

Lerim uses four model roles, each independently configurable:

| Role | Purpose |
|------|---------|
| `lead` | Orchestrates chat, sync, maintain flows (PydanticAI agent) |
| `explorer` | Read-only subagent for candidate gathering |
| `extract` | DSPy extraction pipeline |
| `summarize` | DSPy summarization pipeline |

Default model config (all roles use OpenRouter with Grok):

```toml
[roles.lead]
provider = "openrouter"               # zai | openrouter | openai
model = "x-ai/grok-4.1-fast"
timeout_seconds = 300
max_iterations = 24

[roles.explorer]
provider = "openrouter"
model = "x-ai/grok-4.1-fast"
timeout_seconds = 180
max_iterations = 16

[roles.extract]
provider = "openrouter"
model = "x-ai/grok-4.1-fast"
sub_model = "x-ai/grok-4.1-fast"
timeout_seconds = 180
max_llm_calls = 12

[roles.summarize]
provider = "openrouter"
model = "x-ai/grok-4.1-fast"
sub_model = "x-ai/grok-4.1-fast"
timeout_seconds = 180
max_llm_calls = 12
```

### Switching providers

To use OpenAI instead of OpenRouter:

```toml
# ~/.lerim/config.toml
[roles.lead]
provider = "openai"
model = "gpt-4o"

[roles.extract]
provider = "openai"
model = "gpt-4o"
sub_model = "gpt-4o-mini"
```

### Using local models via Ollama

```toml
[roles.extract]
provider = "openrouter"
model = "ollama_chat/qwen3:8b"
api_base = "http://localhost:11434/v1"
```

### Provider-specific options

Each role supports:

| Key | Default | Description |
|-----|---------|-------------|
| `provider` | `openrouter` | Provider name: `openrouter`, `openai`, `zai` |
| `model` | `x-ai/grok-4.1-fast` | Model identifier |
| `sub_model` | same as `model` | Secondary model for DSPy roles |
| `api_base` | — | Custom API base URL |
| `fallback_models` | `[]` | Fallback model list |
| `timeout_seconds` | varies | Request timeout |
| `max_iterations` | varies | Max agent iterations |
| `max_llm_calls` | varies | Max LLM calls (DSPy roles) |
| `openrouter_provider_order` | `[]` | OpenRouter provider routing preference |

## Memory scope

Controls where memories are read from and written to:

| Scope | Behavior |
|-------|----------|
| `project_fallback_global` | Read from project first, fall back to global. Write to project. (default) |
| `project_only` | Read and write only in `<repo>/.lerim/` |
| `global_only` | Read and write only in `~/.lerim/` |

```toml
[memory]
scope = "project_only"
```

## Memory decay

Lerim automatically decays memory confidence over time. Memories that haven't been accessed lose confidence, and those below the archive threshold become archive candidates during `maintain`.

```toml
[memory.decay]
enabled = true
decay_days = 180                  # full decay after 6 months of no access
min_confidence_floor = 0.1        # never decay below 10%
archive_threshold = 0.2           # archive if effective confidence < 20%
recent_access_grace_days = 30     # skip archiving if accessed in last 30 days
```

## Tracing (OpenTelemetry)

Lerim uses PydanticAI's built-in OpenTelemetry instrumentation for agent observability. Traces are sent to [Logfire](https://logfire.pydantic.dev) (free tier).

One-time setup:

```bash
pip install logfire
logfire auth
logfire projects new
```

Enable tracing:

```bash
# env var
LERIM_TRACING=1 lerim sync

# or in config
[tracing]
enabled = true
```

| Key | Default | Description |
|-----|---------|-------------|
| `enabled` | `false` | Enable tracing (or set `LERIM_TRACING=1`) |
| `include_httpx` | `false` | Capture raw HTTP request/response bodies |
| `include_content` | `true` | Include prompt/completion text in spans |
