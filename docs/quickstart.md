# Quickstart

Get Lerim running in under 5 minutes — from installation to your first knowledge query.

## Prerequisites

Before you begin, make sure you have:

- Python 3.10 or higher
- [Docker](https://docs.docker.com/get-docker/) installed (recommended)
- An LLM API key — you only need a key for the provider(s) you configure

!!! tip
    If you don't have Docker, you can run Lerim directly using `lerim serve` instead of `lerim up`. See the [installation guide](installation.md#running-without-docker) for details.

## Get started in 5 steps

<div class="steps" markdown>

<div class="step" markdown>

### Install Lerim

Install Lerim via pip:

```bash
pip install lerim
```

Verify the installation:

```bash
lerim --version
```

You should see output like:

```
lerim, version 0.1.69
```

</div>

<div class="step" markdown>

### Set up API keys

Lerim needs an LLM provider for extraction and querying. Set at least one API key:

=== "OpenCode Go (common default)"

    ```bash
    export OPENCODE_API_KEY="..."
    ```

    The shipped `default.toml` often uses `provider = "opencode_go"` for roles. Set this key unless you override providers in `~/.lerim/config.toml`.

=== "MiniMax + ZAI"

    ```bash
    export MINIMAX_API_KEY="sk-cp-..."
    export ZAI_API_KEY="..."
    ```

    Use when `[roles.*]` uses MiniMax and Z.AI.

=== "OpenRouter"

    ```bash
    export OPENROUTER_API_KEY="sk-or-v1-..."
    ```

=== "OpenAI"

    ```bash
    export OPENAI_API_KEY="sk-..."
    ```

!!! note
    You only need API keys for the providers you configure. Match keys to `[roles.*]` in `~/.lerim/config.toml` (see shipped `src/lerim/config/default.toml` for package defaults). See [model roles](configuration/model-roles.md).

</div>

<div class="step" markdown>

### Initialize and add a project

Run the interactive setup wizard:

```bash
lerim init
```

This will:

- Detect your installed coding agents
- Ask which agents you want to connect
- Write the config to `~/.lerim/config.toml`
- Check for Docker availability

Example session:

```
Welcome to Lerim.

Which coding agents do you use?
  claude (detected) [Y/n]: y
  cursor (detected) [Y/n]: y
  codex (not found) [y/N]: n
  opencode (not found) [y/N]: n

Config written to ~/.lerim/config.toml
Agents: claude, cursor

Docker: found
```

Now add your first project:

```bash
lerim project add .
```

This registers the current directory and creates a `.lerim/` folder for project-specific memories.

!!! tip
    You can add multiple projects. Each project gets its own `.lerim/` directory for scoped memories.

</div>

<div class="step" markdown>

### Start the Lerim service

Start Lerim as a Docker service:

```bash
lerim up
```

Output:

```
Starting Lerim with 1 projects and 2 agents...
Lerim is running at http://localhost:8765
```

This starts a Docker container that:

- Runs the sync + maintain daemon loop
- Exposes the JSON API at `http://localhost:8765` for CLI commands

!!! note
    The first time you run `lerim up`, it will pull the Docker image from the registry. This may take a minute.

Use **[Lerim Cloud](https://lerim.dev)** for the web UI (sessions, memories, settings). `http://localhost:8765/` may show a short stub page linking to Cloud.

</div>

<div class="step" markdown>

### Query your memories

Now you can query your agent memories. Try these commands:

=== "Ask a question"

    ```bash
    lerim ask "Why did we choose this architecture?"
    ```

=== "Search memories"

    ```bash
    lerim memory search "database migration"
    ```

=== "List all memories"

    ```bash
    lerim memory list
    ```

=== "Check status"

    ```bash
    lerim status
    ```

Example output from `lerim status`:

```
Lerim status:
- connected_agents: 2
- memory_count: 0
- sessions_indexed_count: 0
- queue: {'pending': 0, 'processing': 0, 'failed': 0}
```

!!! tip
    If you don't have any memories yet, use your coding agents as usual. Lerim will automatically sync sessions in the background and extract decisions and learnings.

</div>

</div>

---

## Teach your agent about Lerim

Install the Lerim skill so your coding agent knows how to query past context:

```bash
lerim skill install
```

This copies `SKILL.md` and `cli-reference.md` into `~/.agents/skills/lerim/` and
`~/.claude/skills/lerim/` (see `lerim skill install --help`).

At the start of a coding session, tell your agent:

> Check lerim for any relevant memories about [topic you're working on].

Your agent will run `lerim ask` or `lerim memory search` to pull in past decisions and learnings before it starts working.

## Force a sync

By default, Lerim syncs sessions automatically in the background. To trigger a manual sync:

```bash
lerim sync
```

You can limit the number of sessions to sync:

```bash
lerim sync --max-sessions 5
```

Or sync only a specific agent:

```bash
lerim sync --agent claude
```

## Managing the service

=== "Stop the service"

    ```bash
    lerim down
    ```

=== "Restart"

    ```bash
    lerim down && lerim up
    ```

=== "View logs"

    ```bash
    lerim logs
    ```

=== "Tail logs"

    ```bash
    lerim logs --follow
    ```

=== "List projects"

    ```bash
    lerim project list
    ```

---

## What's happening in the background?

1. **Session indexing** — Lerim watches your agent session stores for new traces
2. **Extraction** — When new sessions are detected, Lerim extracts decision and learning candidates using DSPy pipelines
3. **Deduplication** — Candidates are compared against existing knowledge to avoid duplicates
4. **Storage** — New entries are written as markdown files to `.lerim/memory/` (project scope)
5. **Refinement** — The maintain loop periodically merges duplicates, archives low-value entries, and refreshes the memory index

## Next steps

<div class="grid cards" markdown>

-   :material-console: **CLI reference**

    ---

    Master all Lerim commands

    [:octicons-arrow-right-24: CLI Reference](cli/overview.md)

-   :material-cog: **Configuration**

    ---

    Customize model providers, tracing, and more

    [:octicons-arrow-right-24: Configuration](configuration/overview.md)

-   :material-database: **Memory model**

    ---

    Understand how memories are stored and structured

    [:octicons-arrow-right-24: Memory model](concepts/memory-model.md)

-   :material-monitor-dashboard: **Lerim Cloud**

    ---

    Web UI (sessions, memories, pipeline)

    [:octicons-arrow-right-24: Web UI](guides/dashboard.md)

</div>
