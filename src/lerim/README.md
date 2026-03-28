# Lerim Python Package

## Summary

This folder contains Lerim runtime code.
The package is organized by feature boundary:

- `app/`: CLI, daemon loop, HTTP handler (`dashboard.py` serves JSON API + optional static), shared API logic (`api.py`)
- `config/`: config loading, scope resolution, logging, OpenTelemetry tracing
- `sessions/`: platform indexing, session catalog, queue
- `memory/`: memory taxonomy/record schema (`memory_record.py`), repository paths (`memory_repo.py`), extraction pipeline, trace summarization pipeline
- `runtime/`: lead runtime (`oai_agent.py`), OpenAI Agents SDK tools (`oai_tools.py`), provider/model builders (`oai_providers.py`), agent context (`oai_context.py`), shared helpers (`helpers.py`), typed contracts (`contracts.py`), prompt builders (`prompts/`)
- `adapters/`: platform-specific session readers

## How to use

Read these files in order:

1. `app/cli.py` for the public command surface.
2. `app/daemon.py` for sync/maintain execution flow.
3. `sessions/catalog.py` + `memory/extract_pipeline.py` for ingest and extraction.
4. `memory/memory_repo.py` + `memory/memory_record.py` for persisted memory behavior.
