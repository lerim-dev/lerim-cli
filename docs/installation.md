# Installation

Detailed installation instructions for Lerim, including prerequisites, Python setup, Docker configuration, and troubleshooting.

## Prerequisites

Before you begin, make sure you have:

- **Python 3.10 or higher**
- **Docker** installed ([get Docker](https://docs.docker.com/get-docker/)) — recommended for the always-on service
- **An LLM API key** — you only need a key for the provider(s) in your `[roles.*]` config (e.g. `OPENCODE_API_KEY` for OpenCode Go defaults, or MiniMax / Z.AI / OpenRouter / OpenAI / Anthropic as configured)

!!! tip "Docker is optional"
    If you don't have Docker, you can run Lerim directly using `lerim serve` instead of `lerim up`. See [Running without Docker](#running-without-docker) below.

## Install Lerim

=== "pip"

    ```bash
    pip install lerim
    ```

=== "pipx (isolated)"

    ```bash
    pipx install lerim
    ```

=== "uv"

    ```bash
    uv pip install lerim
    ```

Verify the installation:

```bash
lerim --version
```

## Set up API keys

Lerim needs an LLM provider for extraction and querying. Set at least one:

=== "OpenCode Go (common default)"

    ```bash
    export OPENCODE_API_KEY="..."
    ```

    Package defaults often use `provider = "opencode_go"` — set this unless you change `[roles.*]`.

=== "MiniMax + ZAI"

    ```bash
    export MINIMAX_API_KEY="sk-cp-..."
    export ZAI_API_KEY="..."
    ```

    Use when your config uses MiniMax and Z.AI.

=== "OpenRouter"

    ```bash
    export OPENROUTER_API_KEY="sk-or-v1-..."
    ```

=== "OpenAI"

    ```bash
    export OPENAI_API_KEY="sk-..."
    ```

!!! note
    You only need API keys for the providers you configure. Match keys to `[roles.*]` (see shipped `src/lerim/config/default.toml`). See [model roles](configuration/model-roles.md).

## First-time setup

Run the interactive setup wizard:

```bash
lerim init
```

This will:

1. Detect your installed coding agents (Claude Code, Codex, Cursor, OpenCode)
2. Ask which agents you want to connect
3. Write the config to `~/.lerim/config.toml`
4. Check for Docker availability

Then register your projects:

```bash
lerim project add .                     # current directory
lerim project add ~/codes/my-app        # another project
```

## Start Lerim

=== "Docker (recommended)"

    ```bash
    lerim up
    ```

    This starts a Docker container with the daemon + JSON API on `http://localhost:8765` (web UI: [Lerim Cloud](https://lerim.dev)).

=== "Without Docker"

    ```bash
    lerim connect auto          # detect agent platforms
    lerim serve                 # JSON API + daemon loop (web UI: Lerim Cloud)
    ```

## Running without Docker

If you prefer not to use Docker, run Lerim directly:

```bash
lerim connect auto           # detect agent platforms
lerim serve                  # JSON API + daemon loop
```

Then use `lerim ask`, `lerim sync`, `lerim status`, etc. as usual — they connect to the running server.

## Local models (Ollama)

To use local models instead of cloud APIs:

1. Install Ollama: [ollama.com](https://ollama.com)
2. Pull a model: `ollama pull qwen3.5:9b-q8_0`
3. Make sure Ollama is running: `ollama serve` (or the macOS background service)
4. Configure Lerim roles to use Ollama:

```toml
# ~/.lerim/config.toml
[roles.lead]
provider = "ollama"
model = "qwen3.5:9b-q8_0"

[roles.extract]
provider = "ollama"
model = "qwen3.5:9b-q8_0"
```

Lerim automatically loads models into RAM before each sync/maintain cycle and
unloads them immediately after, so the model only uses memory during active
processing. No API keys required.

If running Lerim in Docker with Ollama on the host:

```toml
[providers]
ollama = "http://host.docker.internal:11434"
```

## Troubleshooting

### Docker not found

If `lerim up` reports Docker is not found:

```bash
# Check Docker installation
docker --version

# On macOS, make sure Docker Desktop is running
open -a Docker
```

### API key errors

If sync or ask commands fail with authentication errors, confirm the env var for
your configured `provider` (e.g. `echo $OPENCODE_API_KEY` for OpenCode Go) and
re-export it, or switch `[roles.*]` to a provider you have keys for.

### Port already in use

If port 8765 is occupied:

```bash
# Use a custom port
lerim serve --port 9000

# Or stop whatever is using 8765
lsof -i :8765
```

### Fresh start

If you need to reset everything:

```bash
# Reinitialize config (preserves memories)
lerim init

# Or wipe all data and start over
lerim memory reset --scope both --yes
lerim down
lerim up
```

!!! warning
    `lerim memory reset` permanently deletes all memories, workspace data, and session indexes. This cannot be undone.

## Next steps

<div class="grid cards" markdown>

-   :material-rocket-launch: **Quickstart**

    ---

    Complete the 5-minute quickstart guide

    [:octicons-arrow-right-24: Quickstart](quickstart.md)

-   :material-cog: **Configuration**

    ---

    Customize model providers, tracing, and more

    [:octicons-arrow-right-24: Configuration](configuration/overview.md)

</div>
