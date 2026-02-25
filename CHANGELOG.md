# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-02-25

### Added

- Continual learning layer for coding agents and projects.
- Platform adapters for Claude Code, Codex CLI, Cursor, and OpenCode.
- Memory extraction pipeline using DSPy RLM to extract decisions and learnings from coding session traces.
- Trace summarization pipeline using DSPy RLM to produce structured summaries with YAML frontmatter.
- PydanticAI lead agent with a read-only explorer subagent for memory operations.
- Three CLI flows: `sync` (extract, summarize, write memories), `maintain` (merge, archive, decay), and `chat` (query memories).
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
