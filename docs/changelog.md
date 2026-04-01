# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-03-25

### Changed

- **Migrated from PydanticAI to DSPy ReAct** -- the lead agent now runs on DSPy ReAct modules (`SyncAgent`, `MaintainAgent`, `AskAgent`). All providers are supported via `dspy.LM` through unified `providers.py`.
- **Removed explorer subagent** -- search, read, and writes go through DSPy ReAct tool functions on the lead agent (e.g. `read_file`, `list_files`, `memory_search`, `write_memory`) instead of a nested explorer.
- Removed `max_explorers` config option (no longer applicable).
- Removed `[roles.explorer]` config section.
- Runtime module reorganized: `agent.py` replaced by `runtime.py`, `tools.py`/`subagents.py` replaced by `tools.py`, `providers.py`, `context.py`, `helpers.py`.

### Removed

- PydanticAI dependency and all PydanticAI-specific code.

## [0.1.65] - 2026-03-14

### Added

- **Evaluation framework** — dataset pipeline to build benchmarks from real traces, LLM-as-judge scoring with Claude CLI / Codex / OpenCode, per-model eval configs (MiniMax, Ollama Qwen 3.5 4B/9B/35B), and benchmark script for model comparison.
- **Trace compaction** — Claude and Codex adapters strip noise lines (progress updates, file snapshots, context dumps) before extraction. Claude ~80% reduction, Codex ~65%. Cached in `~/.lerim/cache/{claude,codex}/`.
- **Parallel window processing** — DSPy extraction and summarization pipelines process transcript windows in parallel via `ThreadPoolExecutor`. Controlled by `max_workers` (default: 4).
- **JSONL-boundary windowing** — transcript windows split on line boundaries instead of mid-JSON.
- **`max_explorers` config** — controls parallel explorer subagents per lead turn (default: 4).
- **`max_workers` config** — controls parallel window processing threads (default: 4).
- **`thinking` config** — controls model reasoning mode on all four roles (default: true).
- Fallback model support in DSPy pipelines (e.g. MiniMax -> Z.AI on rate limits).
- 455 unit tests, all passing.

### Changed

- **Pipeline optimization** — switched from `ChainOfThought` to `Predict` for fewer failures and lower latency. `XMLAdapter` hardcoded (94% success rate).
- **Simplified DSPy signatures** — removed metadata/metrics from LLM inputs, slimmed output schemas.
- **ID-based session skip** — run ID membership check instead of SHA-256 content hashing.
- DSPy pipelines use `Refine(N=2)` for retry on validation failure.
- Fixed conftest skip scoping, `_toml_value` list serialization, integration test provider config.
- Reduced xdist workers from auto(16) to 4 for API rate limit safety.

## [0.1.60] - 2026-03-05

### Added

- **Ollama lifecycle management** — automatic model load/unload before and after each sync/maintain cycle. Controlled by `auto_unload` in `[providers]` (default: true).
- **vllm-mlx provider** — Apple Silicon local model support via `provider = "mlx"`.
- **LiteLLM proxy integration** — routes Ollama think-off requests through LiteLLM for PydanticAI compatibility.
- Docker networking for host Ollama access (`host.docker.internal`).
- Eval runners for sync and maintain pipelines with judge prompts and trace files.
- Eval configs organized under `evals/configs/`.

## [0.1.55] - 2026-03-02

### Added

- **`lerim skill install` command** — installs Lerim skill files (SKILL.md, cli-reference.md) directly into coding agent directories. No `npx`, no git clone needed — skill files are bundled with the pip package.
- Bundled skill files in `src/lerim/skills/` included as package data.
- Updated skill CLI reference with missing `daemon --max-sessions` and `dashboard --port` flags.

## [0.1.54] - 2026-03-02

### Added

- **MiniMax provider support** — MiniMax Coding Plan (`https://api.minimax.io/v1`) now available as a provider. MiniMax-M2.5 is the new default model for all roles.
- **Z.AI Coding Plan endpoint** — Z.AI provider now uses the Coding Plan API endpoint for subscription-based pricing.
- **Fallback model chains** — all roles default to MiniMax (primary) with Z.AI fallback.

### Changed

- Default provider switched from OpenRouter to **MiniMax** across all four roles.
- Default fallback models switched to **Z.AI** (glm-4.7 for lead/explorer, glm-4.5-air for extract/summarize).
- Documentation restructured with Material for MkDocs components.

## [0.1.53] - 2026-03-01

### Fixed

- Daemon loop: maintain never triggered on startup in Docker containers where `time.monotonic()` reflected VM uptime smaller than the maintain interval.
- Daemon loop: sync/maintain cycles produced zero log output, making `lerim logs` appear idle.
- Session queue: NULL `repo_path` jobs clogged the claim queue. Added filter and guard.
- DB migration: orphaned NULL `repo_path` pending/failed jobs purged on schema init.
- Explorer subagent: switched from structured output to plain `str` to avoid validation failures.
- Explorer failures no longer crash the lead agent; returns empty evidence with warning.
- Maintain action path validation: handle list-valued paths from LLM output.
- `run_maintain_once` accepts a `trigger` parameter instead of hardcoding `"manual"`.

## [0.1.52] - 2026-03-01

### Changed

- **Chronological memory processing** — sync and maintain process memories in strict oldest-first order.
- Adapters sort sessions by `start_time` before returning.
- Job queue uses `start_time ASC` (was `DESC`).
- Jobs run sequentially instead of parallel to preserve ordering.
- Removed `sync_max_workers` setting (no longer applicable).

## [0.1.51] - 2026-03-01

### Fixed

- Stream Docker pull/build output to terminal so users see real-time progress during `lerim up`.

## [0.1.5] - 2026-03-01

### Added

- **Per-run LLM cost tracking** via OpenRouter's `usage.cost` response field. PydanticAI calls captured via httpx response hook; DSPy calls captured from LM history. Cost logged in `activity.log` and returned in API responses.
- Activity log format: `timestamp | op | project | stats | $cost | duration`.
- Multi-project maintain (iterates all registered projects).

### Changed

- Replaced raw markdown `write` tool with structured `write_memory` tool. LLM passes fields; Python builds markdown. Eliminates frontmatter format errors.
- `write_file_tool` raises `ModelRetry` for memory paths, directing LLM to `write_memory`.
- Sync prompt restructured with explicit batching instructions.
- `_process_claimed_jobs` runs sequentially (was parallel) for chronological consistency.

### Fixed

- `lerim down` checks if container is running before attempting stop.
- Docker restart policy changed from `unless-stopped` to `"no"` — no silent auto-restart.

### Infrastructure

- Added `pytest-xdist` for parallel test execution (~2x speedup for e2e).

## [0.1.4] - 2026-02-28

### Fixed

- Multi-platform Docker build (amd64 + arm64).

## [0.1.3] - 2026-02-28

### Fixed

- Add OCI source label to Dockerfile for GHCR repo linking.
- Install ripgrep in CI for memory search tests.

## [0.1.2] - 2026-02-28

### Added

- **GHCR Docker publishing** — container images published to GitHub Container Registry.
- **Per-project memory isolation** — each registered project gets its own `.lerim/` directory.

## [0.1.1] - 2026-02-28

### Changed

- Renamed `lerim chat` to `lerim ask` across CLI, API, dashboard, tests, and docs.
- `lerim memory add` sets `source: cli` in frontmatter and uses canonical filename format.
- `grep_files_tool` uses ripgrep (`rg`) subprocess instead of Python regex.
- Simplified `tools.py` by removing 11 over-engineered helper functions (~150 lines removed).
- `run_daemon_once` accepts `max_sessions` parameter for bounded processing.

### Fixed

- Config `_parse_string_table` handles dict values from TOML tables.
- Provider settings deep merge no longer re-adds removed keys.
- Daemon crash on malformed session traces.
- Agent tool call failures from overly complex tool signatures.
- Ask missing memories due to search scope issues.
- Memory search returning stale results.
- Sync pipeline errors on edge-case transcripts.

### Removed

- `lerim memory export` command and handler.
- `search_memory`, `read_memory_frontmatter`, `list_memory_files` from `api.py`.

### Infrastructure

- Dockerfile updated with `ripgrep` package.

## [0.1.0] - 2026-02-25

### Added

- Continual learning layer for coding agents and projects.
- Platform adapters for Claude Code, Codex CLI, Cursor, and OpenCode.
- Memory extraction pipeline using DSPy with transcript windowing.
- Trace summarization pipeline using DSPy with transcript windowing.
- PydanticAI lead agent with read-only explorer subagent.
- Three CLI flows: `sync`, `maintain`, and `ask`.
- Daemon mode for continuous sync and maintain loop.
- Local web dashboard.
- Session catalog with SQLite FTS5.
- Job queue with stale job reclamation.
- TOML-layered configuration.
- OpenTelemetry tracing via Logfire.
- Multi-provider LLM support: MiniMax, Z.AI, OpenRouter, Ollama, OpenAI, vllm-mlx.
- File-first memory model using markdown files with YAML frontmatter.
- Memory primitives: decisions, learnings, and summaries.
- Comprehensive test suite across unit, smoke, integration, and e2e layers.
