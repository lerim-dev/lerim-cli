# Lerim Python Package

## Summary

This folder contains the Lerim runtime package.
Current architecture is PydanticAI-only for agent execution.

The package is organized by feature boundary:

- `agents/`: agent flows (`extract.py`, `maintain.py`, `ask.py`), memory tools (`tools.py`), typed contracts (`contracts.py`)
- `server/`: CLI (`cli.py`), HTTP API (`httpd.py`), daemon (`daemon.py`), runtime orchestrator (`runtime.py`), Docker/runtime API helpers (`api.py`)
- `config/`: config loading (`settings.py`), PydanticAI model builders (`providers.py`), tracing and logging setup
- `memory/`: memory layout and repo-scoped paths (`repo.py`), trace formatting helpers (`transcript.py`)
- `sessions/`: session catalog and queue state (`catalog.py`)
- `adapters/`: session readers for Claude, Codex, Cursor, OpenCode
- `cloud/`: hosted auth/shipper integration (`auth.py`, `shipper.py`)
- `skills/`: bundled skill markdown files

## How to use

If you are new to the codebase, read in this order:

1. `server/cli.py` for the public command surface.
2. `server/daemon.py` for sync/maintain scheduling and lock flow.
3. `server/runtime.py` for runtime orchestration across extract/maintain/ask.
4. `agents/tools.py` for memory tool functions (`read`, `grep`, `scan`, `write`, `edit`, `archive`, `verify_index`).
5. `agents/extract.py`, `agents/maintain.py`, `agents/ask.py` for PydanticAI agent behavior.
6. `memory/repo.py` for on-disk layout under project and global scopes.
