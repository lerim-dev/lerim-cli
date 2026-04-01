# Model Roles

Lerim separates **orchestration** (DSPy ReAct lead), **DSPy extraction**, and **DSPy summarization**. A **`[roles.codex]`** block is also parsed for config (e.g. Cloud UI); it is **not** consumed by `LerimRuntime` at runtime today.

## Runtime roles

| Role | Runtime | Purpose | Default model |
|------|---------|---------|---------------|
| `lead` | DSPy ReAct | Orchestrates sync/maintain/ask via tools (`write_memory`, `extract_pipeline`, `memory_search`, ...); only path that writes memory | See `default.toml` |
| `extract` | DSPy | Extracts decision and learning candidates from session transcripts | See `default.toml` |
| `summarize` | DSPy | Generates structured session summaries from transcripts | See `default.toml` |

## Config-only role

| Role | Purpose |
|------|---------|
| `codex` | Parsed into `Config` (e.g. for Lerim Cloud). Reserved for future use — **not** wired into the lead runtime. |

```mermaid
flowchart LR
	Transcript[SessionTranscript] --> Ext[extract_DSPy]
	Transcript --> Sum[summarize_DSPy]
	Ext --> Lead[lead_OAI_agent]
	Sum --> Lead
	Lead --> Tools[SDK_tools]
```

Tools include DSPy pipeline tools invoked by the lead agent and filesystem/search helpers as defined in `src/lerim/runtime/tools.py`.

## Role configuration

Each role is configured under `[roles.<name>]` in your TOML config.

=== "Lead"

	```toml
	[roles.lead]
	provider = "minimax"
	model = "MiniMax-M2.5"
	api_base = ""
	fallback_models = ["zai:glm-4.7"]
	timeout_seconds = 300
	max_iterations = 10
	openrouter_provider_order = []
	thinking = true
	```

	The lead agent is the only component allowed to write memory files. It
	orchestrates the full sync, maintain, and ask flows. Uses `dspy.LM`
	through unified `providers.py` to support all providers.

=== "Extract"

	```toml
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
	max_workers = 4
	```

	Extraction runs through `dspy.ChainOfThought` with transcript windowing.
	Large transcripts are split into overlapping windows of `max_window_tokens`,
	processed independently, then merged in a final call.

=== "Summarize"

	```toml
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
	max_workers = 4
	```

	Summarization uses the same windowed ChainOfThought approach as extraction,
	producing structured summaries with frontmatter.

=== "Codex (config)"

	```toml
	[roles.codex]
	provider = "opencode_go"
	model = "minimax-m2.5"
	api_base = ""
	timeout_seconds = 600
	idle_timeout_seconds = 120
	```

	This block is loaded for config visibility (e.g. Cloud) and future use. **Changing it does not affect the current DSPy ReAct lead runtime.**

## Provider support

All providers (MiniMax, Z.AI, Ollama, OpenAI, Anthropic, OpenRouter, etc.) are
supported through `dspy.LM` via unified `providers.py`. No proxy layer is
needed -- `dspy.LM` handles Chat Completions natively for all backends.
Configuration is the same across providers.

## Switching providers

You can point any role at a different provider by changing `provider` and `model`.

### Use OpenAI directly

```toml
[roles.lead]
provider = "openai"
model = "gpt-5"
```

Requires `OPENAI_API_KEY` in your environment.

### Use Z.AI (Coding Plan)

```toml
[roles.lead]
provider = "zai"
model = "glm-4.7"
```

Requires `ZAI_API_KEY` in your environment.

### Use Anthropic via OpenRouter

```toml
[roles.lead]
provider = "openrouter"
model = "anthropic/claude-sonnet-4-20250514"
```

Requires `OPENROUTER_API_KEY` in your environment. OpenRouter proxies the
request to Anthropic.

### Use Ollama (local models)

```toml
[roles.extract]
provider = "ollama"
model = "qwen3:32b"
api_base = "http://127.0.0.1:11434"
```

No API key required. Make sure Ollama is running locally (`ollama serve` or the
macOS background service). Lerim automatically loads models into RAM before each
sync/maintain cycle and unloads them immediately after, so the model only uses
memory during active processing. Disable this with `auto_unload = false` in
`[providers]`.

Override the `api_base` per-role or set the default in `[providers]`:

```toml
[providers]
ollama = "http://127.0.0.1:11434"
auto_unload = true   # free model RAM between cycles (default)
```

If Lerim runs in Docker and Ollama on the host, use `host.docker.internal`:

```toml
[providers]
ollama = "http://host.docker.internal:11434"
```

### Use vllm-mlx (Apple Silicon local models)

```toml
[roles.extract]
provider = "mlx"
model = "mlx-community/Qwen3.5-4B-Instruct-4bit"
```

No API key required. Requires [vllm-mlx](https://github.com/vllm-project/vllm-mlx)
running locally (`pip install vllm-mlx`). Start the server with:

```bash
vllm-mlx serve mlx-community/Qwen3.5-4B-Instruct-4bit --port 8000
```

Override the default base URL per-role or in `[providers]`:

```toml
[providers]
mlx = "http://127.0.0.1:8000/v1"
```

!!! tip "Cost optimization"
	Use a cheaper/faster model for `extract` and `summarize` (high-volume DSPy
	tasks) and a more capable model for `lead` (orchestration and reasoning).

## Common options

All roles share these configuration keys:

| Option | Description |
|--------|-------------|
| `provider` | Backend: `minimax`, `zai`, `openrouter`, `openai`, `ollama`, `mlx`, `opencode_go`, … |
| `model` | Model identifier (for OpenRouter, use the full slug e.g. `anthropic/claude-sonnet-4-5-20250929`) |
| `api_base` | Custom API endpoint. Empty = use default from `[providers]` |
| `fallback_models` | Ordered fallback chain: `"model"` (same provider) or `"provider:model"` |
| `timeout_seconds` | HTTP request timeout in seconds |
| `thinking` | Enable model reasoning (default: `true`, set `false` for non-reasoning models) |

**Orchestration role** (`lead`) also has: `max_iterations`.

**DSPy roles** (`extract`, `summarize`) also have: `max_window_tokens`, `window_overlap_tokens`, `max_workers` (default: 4, set 1 for local models).

## Fallback models

When a primary model fails, Lerim tries each fallback in order:

```toml
[roles.extract]
provider = "minimax"
model = "MiniMax-M2.5"
fallback_models = ["zai:glm-4.5-air", "openai:gpt-4.1-mini"]
```

- `"model-slug"` -- uses the same provider as the role
- `"provider:model-slug"` -- uses a different provider (requires that provider's API key)

## API key resolution

| Provider | Environment variable |
|----------|---------------------|
| `minimax` | `MINIMAX_API_KEY` |
| `zai` | `ZAI_API_KEY` |
| `openrouter` | `OPENROUTER_API_KEY` |
| `openai` | `OPENAI_API_KEY` |
| `ollama` | *(none required)* |
| `mlx` | *(none required)* |

!!! warning "Missing keys"
	If the required API key for a role's provider is not set, Lerim raises an
	error at startup. There is no silent fallback.
