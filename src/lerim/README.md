# Lerim Python Package

## Summary

This folder contains Lerim runtime code.
The package is organized by feature boundary:

- `app/`: CLI, daemon loop, HTTP handler (`dashboard.py` serves JSON API + optional static), shared API logic (`api.py`)
- `config/`: config loading, scope resolution, logging, OpenTelemetry tracing
- `sessions/`: platform indexing, session catalog, queue
- `memory/`: memory taxonomy/record schema (`memory_record.py`), repository paths (`memory_repo.py`), extraction pipeline, trace summarization pipeline
- `runtime/`: lead runtime (`runtime.py`), DSPy ReAct agents (`sync_agent.py`, `maintain_agent.py`, `ask_agent.py`), tool functions (`tools.py`), runtime context (`context.py`), provider/model builders (`providers.py`), shared helpers (`helpers.py`), typed contracts (`contracts.py`), prompt builders (`prompts/`)
- `adapters/`: platform-specific session readers

## How to use

Read these files in order:

1. `app/cli.py` for the public command surface.
2. `app/daemon.py` for sync/maintain execution flow.
3. `sessions/catalog.py` + `memory/extract_pipeline.py` for ingest and extraction.
4. `memory/memory_repo.py` + `memory/memory_record.py` for persisted memory behavior.
