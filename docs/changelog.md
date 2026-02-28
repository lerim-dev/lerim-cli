# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.1] - 2026-02-28

### Changed

- Renamed `lerim chat` to `lerim ask` across CLI, API, dashboard, tests, and docs.
- `lerim memory add` now sets `source: cli` in frontmatter and uses canonical filename format.
- `grep_files_tool` now uses ripgrep (`rg`) subprocess instead of Python regex.
- Simplified `tools.py` by removing 11 over-engineered helper functions (~150 lines removed).
- Improved agent tool docstrings, explorer subagent system prompt, and ask prompt with search strategy guidance.
- `run_daemon_once` accepts `max_sessions` parameter for bounded processing.

### Fixed

- Config `_parse_string_table` handles dict values from TOML tables (BUG-1).
- Provider settings deep merge no longer re-adds removed keys (BUG-2).
- Daemon crash on malformed session traces (BUG-3).
- Agent tool call failures from overly complex tool signatures (BUG-4).
- Ask missing memories due to search scope issues (BUG-5).
- Memory search returning stale results (BUG-6).
- Sync pipeline errors on edge-case transcripts (BUG-7).

### Removed

- `lerim memory export` command and handler.
- `search_memory`, `read_memory_frontmatter`, `list_memory_files` from `api.py` (inlined where needed).

### Infrastructure

- Dockerfile updated with `ripgrep` package.

## [0.1.0] - 2026-02-25

### Added

- Continual learning layer for coding agents and projects.
- Platform adapters for Claude Code, Codex CLI, Cursor, and OpenCode.
- Memory extraction pipeline using DSPy RLM to extract decisions and learnings from coding session traces.
- Trace summarization pipeline using DSPy RLM to produce structured summaries with YAML frontmatter.
- PydanticAI lead agent with a read-only explorer subagent for memory operations.
- Three CLI flows: `sync` (extract, summarize, write memories), `maintain` (merge, archive, decay), and `ask` (query memories).
- Daemon mode for continuous sync and maintain loop.
- Local read-only web dashboard.
- Session catalog with SQLite FTS5 for session search.
- Job queue with stale job reclamation.
- TOML-layered configuration: shipped defaults, global, project, and env var override.
- OpenTelemetry tracing via Logfire with PydanticAI and DSPy instrumentation.
- Multi-provider LLM support: OpenRouter (with Nebius routing), Ollama, ZAI, OpenAI, Anthropic.
- File-first memory model using markdown files with YAML frontmatter.
- Project-first memory scope with global fallback.
- Memory primitives: decisions, learnings, and summaries.
- Comprehensive test suite with 290 tests across unit, smoke, integration, and e2e layers.
- Skills distribution via `npx skills add lerim-dev/lerim-cli`.
