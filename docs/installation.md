# Installation

Detailed installation instructions for Lerim, including prerequisites, Python setup, Docker configuration, and troubleshooting.

## Prerequisites

Before you begin, make sure you have:

- **Python 3.10 or higher**
- **Docker** installed ([get Docker](https://docs.docker.com/get-docker/)) — recommended for the always-on service
- **An LLM API key** — you only need a key for the provider(s) you configure (MiniMax, Z.AI, OpenRouter, OpenAI, or Anthropic)

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

=== "MiniMax + ZAI (recommended)"

    ```bash
    export MINIMAX_API_KEY="sk-cp-..."
    export ZAI_API_KEY="..."
    ```

    MiniMax is the default provider (MiniMax-M2.5 for all roles) with Z.AI as fallback. Both use subscription-based coding plans for low, predictable costs.

=== "OpenRouter"

    ```bash
    export OPENROUTER_API_KEY="sk-or-v1-..."
    ```

=== "OpenAI"

    ```bash
    export OPENAI_API_KEY="sk-..."
    ```

!!! note
    You only need API keys for the providers you configure. The defaults use MiniMax (primary) with Z.AI (fallback), but you can switch to any supported provider by updating `[roles.*]` in your config. See [model roles](configuration/model-roles.md).

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

    This starts a Docker container with the daemon + HTTP API + dashboard on `http://localhost:8765`.

=== "Without Docker"

    ```bash
    lerim connect auto          # detect agent platforms
    lerim serve                 # start API server + dashboard + daemon loop
    ```

## Running without Docker

If you prefer not to use Docker, run Lerim directly:

```bash
lerim connect auto           # detect agent platforms
lerim serve                  # start API server + dashboard + daemon loop
```

Then use `lerim ask`, `lerim sync`, `lerim status`, etc. as usual — they connect to the running server.

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

If sync or ask commands fail with authentication errors:

```bash
# Verify your key is set
echo $MINIMAX_API_KEY

# Re-export if needed
export MINIMAX_API_KEY="sk-cp-..."
```

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
