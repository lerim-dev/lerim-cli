# Getting Started

Get Lerim running in under 5 minutes.

## Prerequisites

- Python 3.10+
- [Docker](https://docs.docker.com/get-docker/) (for the always-on service)
- An LLM API key (OpenRouter, OpenAI, or Anthropic)

## 1. Install

```bash
pip install lerim
```

## 2. Set up API keys

Lerim needs an LLM provider for extraction and querying. Set at least one:

```bash
export OPENROUTER_API_KEY="sk-or-..."   # default provider
# or
export OPENAI_API_KEY="sk-..."
# or
export ZAI_API_KEY="..."
```

## 3. Initialize

Run the interactive setup wizard:

```bash
lerim init
```

This detects your installed coding agents (Claude Code, Codex, Cursor, OpenCode)
and writes the config to `~/.lerim/config.toml`.

## 4. Add your projects

Register the projects you want Lerim to track:

```bash
lerim project add ~/codes/my-app
lerim project add ~/work/backend
lerim project add .                   # current directory
```

Each registered project gets a `.lerim/` directory for its memories.

## 5. Start Lerim

```bash
lerim up
```

This starts a Docker container that runs the daemon (sync + maintain loop) and
serves the dashboard + HTTP API on `http://localhost:8765`.

## 6. Query your memories

```bash
lerim ask "What auth pattern do we use?"
lerim memory search "database migration"
lerim memory list
lerim status
```

These commands are thin HTTP clients that forward to the running server.

## 7. Teach your agent about Lerim

Install the Lerim skill so your coding agent knows how to query past context:

```bash
npx skills add lerim-dev/lerim-cli
```

This works with Claude Code, Codex, Cursor, Copilot, Cline, Windsurf, OpenCode, and [other agents that support skills](https://skills-ai.dev).

At the start of a session, tell your agent:

> Check lerim for any relevant memories about [topic you're working on].

## Managing the service

```bash
lerim down                  # stop the container
lerim up                    # start again (recreates the container)
lerim logs                  # view logs
lerim logs --follow         # tail logs
lerim project list          # list registered projects
lerim project remove my-app # unregister a project
```

## Running without Docker

If you prefer not to use Docker, run `lerim serve` directly:

```bash
brew install deno            # Deno is required for extraction
lerim connect auto           # detect agent platforms
lerim serve                  # start API server + dashboard + daemon loop
```

Then use `lerim ask`, `lerim sync`, `lerim status`, etc. as usual — they
connect to the running server.

## Next steps

- [CLI Reference](cli-reference.md) — full command documentation
- [Configuration](configuration.md) — TOML config, model roles, tracing
- [Connecting Agents](adapters.md) — supported platforms and custom paths
- [Memory Model](memory-model.md) — how memories are stored and structured
- [Dashboard](dashboard.md) — local web UI for browsing sessions and memories
