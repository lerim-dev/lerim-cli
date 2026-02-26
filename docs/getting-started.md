# Getting Started

Get Lerim running in under 2 minutes.

## Prerequisites

- Python 3.10+
- [Deno](https://deno.land/) (required by the DSPy extraction pipeline)
- An LLM API key (OpenRouter, OpenAI, or Anthropic)

```bash
brew install deno  # macOS
```

## Install

```bash
pip install lerim
```

For development installs:

```bash
uv venv && source .venv/bin/activate
uv pip install -e .
```

## Set up API keys

Lerim needs an LLM provider for extraction and chat. Set at least one:

```bash
export OPENROUTER_API_KEY="sk-or-..."   # default provider
# or
export OPENAI_API_KEY="sk-..."
# or
export ZAI_API_KEY="..."
```

## Connect your agent platforms

Auto-detect and connect all supported platforms:

```bash
lerim connect auto
```

Or connect specific platforms:

```bash
lerim connect claude
lerim connect codex
lerim connect cursor
lerim connect opencode
```

Check what's connected:

```bash
lerim connect list
```

## Start the learning loop

Run the daemon for continuous sync + maintain:

```bash
lerim daemon
```

Or run one-shot commands:

```bash
lerim sync       # extract memories from new sessions
lerim maintain   # refine existing memories
```

## Query your memories

```bash
lerim chat "What auth pattern do we use?"
lerim memory search "database migration"
lerim memory list
```

## Teach your agent about Lerim

Install the Lerim skill so your coding agent knows how to query past context:

```bash
npx skills add lerim-dev/lerim-cli
```

This works with Claude Code, Codex, Cursor, Copilot, Cline, Windsurf, OpenCode, and [other agents that support skills](https://skills-ai.dev).

At the start of a session, tell your agent:

> Check lerim for any relevant memories about [topic you're working on].

Your agent will run `lerim chat` or `lerim memory search` to pull in past decisions and learnings.

## Next steps

- [CLI Reference](cli-reference.md) — full command documentation
- [Configuration](configuration.md) — TOML config, model roles, tracing
- [Connecting Agents](adapters.md) — supported platforms and custom paths
- [Memory Model](memory-model.md) — how memories are stored and structured
- [Dashboard](dashboard.md) — local web UI for browsing sessions and memories
