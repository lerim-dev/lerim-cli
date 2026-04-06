# Model Roles

Lerim's runtime uses a **single DSPy language model** for all orchestration: **`[roles.agent]`** powers `ExtractAgent` (sync), `MaintainAgent`, and `AskAgent` via `dspy.context(lm=...)`.

The role name is enforced in code as `DSPyRoleName = Literal["agent"]` (in `lerim.config.providers`).

## Runtime roles

| Role | Used by | Purpose |
|------|---------|---------|
| `agent` | `LerimRuntime` | **Only** LLM for DSPy ReAct: sync, maintain, ask. Tools are methods on `MemoryTools` in `lerim.agents.tools` (`read`, `grep`, `scan`, `write`, `edit`, `archive`). |

## Architecture

```mermaid
flowchart LR
	subgraph rt [LerimRuntime]
		AgentLM["dspy.LM roles.agent"]
		EA[ExtractAgent]
		MA[MaintainAgent]
		AA[AskAgent]
	end
	AgentLM --> EA
	AgentLM --> MA
	AgentLM --> AA
```

## Role configuration

The single role is configured under `[roles.agent]` in your TOML config:

```toml
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
```

The agent model runs **DSPy ReAct** for all user-facing flows. It calls bound methods on `MemoryTools` (defined in `lerim.agents.tools`) through `dspy.ReAct`.

## Provider support

Providers are configured via `provider` + `[providers]` default URLs. See [config.toml](config-toml.md).

Supported providers: `minimax`, `opencode_go`, `zai`, `openai`, `openrouter`, `ollama`, `mlx`.

## Switching providers

You can point the **agent** role at any supported backend:

### Use MiniMax directly (default)

```toml
[roles.agent]
provider = "minimax"
model = "MiniMax-M2.5"
```

Requires `MINIMAX_API_KEY` in your environment.

### Use OpenAI directly

```toml
[roles.agent]
provider = "openai"
model = "gpt-5"
```

Requires `OPENAI_API_KEY` in your environment.

### Use OpenCode Go

```toml
[roles.agent]
provider = "opencode_go"
model = "minimax-m2.5"
```

Requires `OPENCODE_API_KEY` (or your provider's env var as documented for that backend).

### Use Ollama (local models)

```toml
[roles.agent]
provider = "ollama"
model = "qwen3:32b"
api_base = "http://127.0.0.1:11434"
```

No API key required. `auto_unload` in `[providers]` frees RAM between cycles.

## Common options

| Option | Description |
|--------|-------------|
| `provider` | Backend: `minimax`, `opencode_go`, `zai`, `openrouter`, `openai`, `ollama`, `mlx` |
| `model` | Model identifier (OpenRouter: full slug) |
| `api_base` | Custom API endpoint |
| `fallback_models` | Ordered fallback chain on quota/rate-limit errors |
| `thinking` | Enable reasoning mode when supported |
| `temperature` | Sampling temperature (default `1.0`) |
| `max_tokens` | Maximum output tokens (default `32000`) |
| `max_iters_sync` | Max ReAct iterations for sync flow |
| `max_iters_maintain` | Max ReAct iterations for maintain flow |
| `max_iters_ask` | Max ReAct iterations for ask flow |
| `openrouter_provider_order` | Preferred provider ordering for OpenRouter |

## Fallback models

When the primary model returns a quota or rate-limit error, `LerimRuntime` retries with each `fallback_models` entry:

```toml
[roles.agent]
provider = "minimax"
model = "MiniMax-M2.5"
fallback_models = ["zai:glm-4.7"]
```

Each entry uses `provider:model` syntax. The runtime builds a separate `dspy.LM` for each fallback and tries them in order.

## API key resolution

| Provider | Environment variable |
|----------|---------------------|
| `minimax` | `MINIMAX_API_KEY` |
| `zai` | `ZAI_API_KEY` |
| `openrouter` | `OPENROUTER_API_KEY` |
| `openai` | `OPENAI_API_KEY` |
| `opencode_go` | `OPENCODE_API_KEY` |
| `ollama` | *(none required)* |
| `mlx` | *(none required)* |

!!! warning "Missing keys"
	If the required API key for the agent role's provider is not set, Lerim raises an error at startup.
