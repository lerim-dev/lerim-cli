# Lerim Python Package

## Summary

This folder contains Lerim runtime code.
The package is organized by feature boundary:

- `agents/`: DSPy ReAct agents (`extract.py`, `maintain.py`, `ask.py`), class-based `MemoryTools` (`tools.py`), typed contracts (`contracts.py`)
- `server/`: CLI entry point (`cli.py`), HTTP API (`httpd.py`), daemon loop (`daemon.py`), runtime orchestrator (`runtime.py`), core API logic (`api.py`)
- `cloud/`: Lerim Cloud integration — data shipper (`shipper.py`), OAuth (`auth.py`)
- `config/`: config loading (`settings.py`), DSPy LM builders (`providers.py`), logging, OpenTelemetry tracing, project scope resolution
- `memory/`: repository paths and directory layout (`repo.py`), trace formatting (`transcript.py`)
- `sessions/`: SQLite FTS session catalog, job queue, service run log (`catalog.py`)
- `adapters/`: platform-specific session readers (claude, codex, cursor, opencode)
- `skills/`: filesystem skill packs

## How to use

Read these files in order:

1. `server/cli.py` for the public command surface.
2. `server/daemon.py` for sync/maintain execution flow.
3. `agents/tools.py` for the `MemoryTools` class (read, grep, scan, write, edit, archive).
4. `agents/extract.py` + `agents/maintain.py` + `agents/ask.py` for the three ReAct agents.
5. `memory/repo.py` for persisted memory layout.
