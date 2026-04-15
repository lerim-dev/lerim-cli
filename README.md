<p align="center">
  <img src="assets/lerim.png" alt="Lerim Logo" width="160">
</p>

<h3 align="center">Background memory agent for coding workflows.</h3>

<p align="center">
  <a href="https://pypi.org/project/lerim/"><img src="https://img.shields.io/pypi/v/lerim?style=flat-square&color=blue" alt="PyPI version"></a>
  <a href="https://pypi.org/project/lerim/"><img src="https://img.shields.io/pypi/pyversions/lerim?style=flat-square" alt="Python versions"></a>
  <a href="https://github.com/lerim-dev/lerim-cli/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-BSL--1.1-green?style=flat-square" alt="License"></a>
  <a href="https://github.com/lerim-dev/lerim-cli/actions"><img src="https://img.shields.io/github/actions/workflow/status/lerim-dev/lerim-cli/ci.yml?style=flat-square&label=tests" alt="Tests"></a>
  <a href="https://github.com/lerim-dev/lerim-cli"><img src="https://img.shields.io/github/stars/lerim-dev/lerim-cli?style=flat-square" alt="GitHub stars"></a>
</p>

<p align="center"><a href="https://lerim.dev/">lerim.dev</a> · <a href="https://docs.lerim.dev/">docs</a> · <a href="https://pypi.org/project/lerim/">pypi</a></p>

# Lerim

Lerim watches coding-agent sessions and builds reusable project memory automatically.

It helps your coding workflow keep memory across sessions and across tools, without vendor lock-in. Instead of losing decisions, reasoning, and project context every time a session ends, Lerim extracts and consolidates that memory in the background and stores it locally as plain markdown.

Supported session adapters today: **Claude Code, Codex CLI, Cursor, and OpenCode**.

## Why Lerim

Coding agents are useful, but they forget too much.

A typical workflow looks like this:

- you work with an agent
- important decisions get made
- the session ends
- the next session starts with less context
- the same reasoning gets repeated again

Lerim fixes that.

It runs as a background memory agent for coding workflows. It watches sessions, extracts durable project memory, consolidates it over time, and lets you inspect or query what the workflow has learned.

## What makes Lerim different

Many tools give you memory infrastructure.

Lerim is different because it is **workflow-native**.

It does not only store memory.  
It actively works on your coding workflow.

Lerim is built around three jobs:

1. **Extract** memory from coding-agent sessions
2. **Consolidate** memory over time
3. **Track** project stream status as work evolves

That means Lerim is not just a database, vector store, or memory SDK.

It is a **background memory agent**.

## What you get

With Lerim, you can:

- keep project decisions across sessions
- preserve reasoning and implementation context
- share memory across different coding agents
- ask questions against past work
- keep memory local and file-based

Memories are stored as plain markdown in:

`<repo>/.lerim/memory/`

with fallback storage under:

`~/.lerim/memory/`

## Quick start

Prerequisites:

- Python 3.10+
- Docker recommended

Install Lerim:

`pip install lerim`

Start the service:

`lerim up`

Check that it is running:

`lerim status`

Or watch live activity:

`lerim status --live`

## What the commands do

### `lerim up`

Starts Lerim in the background.

This is the command you run when you want Lerim to begin watching your workflow and processing memory tasks.

### `lerim status`

Shows service health and current status.

Useful for checking whether Lerim is up and connected.

### `lerim status --live`

Shows live status updates.

This is the best command for demos because it makes background extraction visible.

### `lerim sync`

Indexes sessions and extracts candidate memories from recent work. This is done automatically by the docker container and based on the intervals you set in the config file under `~/.lerim/config.toml` file.

### `lerim maintain`

Improves memory quality over time by merging duplicates, archiving weak items, and refreshing useful memories. This will also runs based on the interval you set in the `~/.lerim/config.toml` file.

### `lerim ask`

Lets you ask questions against accumulated project memory.

Example:

`lerim ask "Why did we choose SQLite for local metadata?"`

## Configuration

`lerim init` can help with setup. Then you can override the configs in the `~/.lerim/config.toml` file.

API keys are read from environment variables, stored by default in:

`~/.lerim/.env`

Example:
```bash
MINIMAX_API_KEY=your-key 
OPENROUTER_API_KEY=your-key` 
OPENAI_API_KEY=your-key` 
ZAI_API_KEY=your-key
```

Example provider config:
```toml
[roles.agent] 
provider = "minimax"
model = "MiniMax-M2.7"  
fallback_models = ["zai:glm-4.7"]
```

## Most-used commands
```bash
lerim status  
lerim status --live  
lerim logs --follow  
lerim queue
lerim queue --failed
lerim memory list --limit 20
```

Setup and management:
```bash
lerim connect auto  
lerim project list  
lerim project remove <name>  
lerim skill install
```

Alternative to Docker:

`lerim serve`

## How Lerim works

Lerim runs three agent flows:

- `sync` for indexing sessions and extracting memories
- `maintain` for improving memory quality over time
- `ask` for answering questions with memory context and citations

This makes Lerim useful not only as storage, but as an ongoing background process for project memory.

## Who Lerim is for

Lerim is for developers who:

- use coding agents regularly
- work across multiple sessions
- switch between different coding tools
- want local, reusable, project-level memory
- want memory continuity without vendor lock-in

## What Lerim is not

Lerim is not just a vector store.

Lerim is not only a memory SDK.

Lerim is not tied to one coding assistant.

It is a background memory agent for coding workflows.

## Docs

- Website: https://lerim.dev
- Docs: https://docs.lerim.dev
- PyPI: https://pypi.org/project/lerim/

## Development
```bash
uv venv && source .venv/bin/activate  
uv pip install -e '.[test]'  
tests/run_tests.sh unit  
tests/run_tests.sh quality
```

## Contributing

Contributions are welcome.

If you want to help, good starting points are:

- session adapters and adding more agents
- extraction quality
- memory consolidation quality
- docs and demo examples

  
- Read the [Contributing Guide](https://docs.lerim.dev/contributing/getting-started/)
- Browse [open issues](https://github.com/lerim-dev/lerim-cli/issues)
- Agent adapter PRs are especially appreciated -- see `src/lerim/adapters/` for examples
