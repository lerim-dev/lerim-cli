<p align="center">
  <img src="assets/lerim.png" alt="Lerim Logo" width="160">
</p>

<h3 align="center">Persistent memory for coding agents.</h3>

<p align="center">
  <a href="https://pypi.org/project/lerim/"><img src="https://img.shields.io/pypi/v/lerim?style=flat-square&color=blue" alt="PyPI version"></a>
  <a href="https://pypi.org/project/lerim/"><img src="https://img.shields.io/pypi/pyversions/lerim?style=flat-square" alt="Python versions"></a>
  <a href="https://github.com/lerim-dev/lerim-cli/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-BSL--1.1-green?style=flat-square" alt="License"></a>
  <a href="https://github.com/lerim-dev/lerim-cli/actions"><img src="https://img.shields.io/github/actions/workflow/status/lerim-dev/lerim-cli/ci.yml?style=flat-square&label=tests" alt="Tests"></a>
  <a href="https://github.com/lerim-dev/lerim-cli"><img src="https://img.shields.io/github/stars/lerim-dev/lerim-cli?style=flat-square" alt="GitHub stars"></a>
</p>

<p align="center"><a href="https://lerim.dev/">lerim.dev</a> · <a href="https://docs.lerim.dev/">docs</a> · <a href="https://pypi.org/project/lerim/">pypi</a></p>

## Summary

Lerim is a memory layer for coding agents.
It watches agent sessions, extracts durable memories with PydanticAI, and saves them as plain markdown in `.lerim/memory/`.

Why teams use it:

- Keep project decisions and reasoning across sessions.
- Share context between different coding agents.
- Ask questions against past work with `lerim ask`.
- Keep data local and file-based.

Supported session adapters today: Claude Code, Codex CLI, Cursor, and OpenCode.

## How to use

Prerequisites: Python 3.10+ and Docker (recommended).

Install and bootstrap:

```bash
pip install lerim
lerim init
lerim project add .
lerim up
```

If you want a local Docker build instead of pulling from GHCR:

```bash
lerim up --build
```

Use `--build` from a source checkout (local `Dockerfile` available). For normal PyPI installs, use `lerim up`.

Daily flow:

```bash
lerim sync
lerim maintain
lerim ask "Why did we choose this approach?"
```

These commands call the running Lerim service (`lerim up` or `lerim serve`).

Quick validation flow before release:

```bash
lerim down
lerim up --build
lerim sync
lerim maintain
lerim ask "What are the latest important memories?"
```

## Runtime model

Lerim runs three PydanticAI-based agent flows:

- `sync`: indexes sessions and extracts memories.
- `maintain`: merges duplicates, archives low-value items, refreshes memory quality.
- `ask`: answers questions with memory context and citations.

Memories are markdown files under project scope (`<repo>/.lerim/memory/`) with fallback in `~/.lerim/memory/`.

## Configuration

`lerim init` can set this up for you.
API keys are read from environment variables (stored in `~/.lerim/.env` by default).

```bash
# ~/.lerim/.env
MINIMAX_API_KEY=your-key
# add provider keys you use:
# OPENROUTER_API_KEY, OPENAI_API_KEY, MINIMAX_API_KEY, ZAI_API_KEY
```

Default provider example (MiniMax):

```toml
[roles.agent]
provider = "minimax"
model = "MiniMax-M2.7"
fallback_models = ["zai:glm-4.7"]
```

## Commands

Most-used commands:

```bash
lerim status
lerim status --live
lerim logs --follow
lerim queue
lerim queue --failed
lerim unscoped --limit 20
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

```bash
lerim serve
```

## Web UI

Web UI is not bundled in this repo yet.
Use `lerim dashboard` for current status and CLI alternatives.

## Docs

- Full docs: [docs.lerim.dev](https://docs.lerim.dev/)
- CLI reference: [`src/lerim/skills/cli-reference.md`](src/lerim/skills/cli-reference.md)
- Package source map: [`src/lerim/README.md`](src/lerim/README.md)

## Development

```bash
uv venv && source .venv/bin/activate
uv pip install -e '.[test]'
tests/run_tests.sh unit
tests/run_tests.sh quality
```

## Contributing

Contributions are welcome.

- Read the [Contributing Guide](https://docs.lerim.dev/contributing/getting-started/)
- Browse [open issues](https://github.com/lerim-dev/lerim-cli/issues)
- Agent adapter PRs are especially appreciated -- see `src/lerim/adapters/` for examples
