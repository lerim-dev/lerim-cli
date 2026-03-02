# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.54] - 2026-03-02

### Added

- **MiniMax provider support** — MiniMax Coding Plan (`https://api.minimax.io/v1`) now available as a provider. MiniMax-M2.5 is the new default model for all roles.
- **Z.AI Coding Plan endpoint** — Z.AI provider now uses the Coding Plan API endpoint (`https://api.z.ai/api/coding/paas/v4`) for subscription-based pricing.
- **FallbackModel chains** — All roles default to MiniMax (primary) with Z.AI fallback for cost-effective, resilient operation.

### Changed

- Default provider switched from OpenRouter to **MiniMax** across all four roles (lead, explorer, extract, summarize).
- Default fallback models switched to **Z.AI** (glm-4.7 for lead/explorer, glm-4.5-air for extract/summarize).
- Documentation enriched with Material for MkDocs components (admonitions, tabs, grids, steps) and restructured navigation.
- Clarified that API keys are only required for providers you actually configure — no keys are universally mandatory.

## [0.1.53] - 2026-03-01

### Fixed

- Daemon loop: maintain never triggered on startup in Docker containers where `time.monotonic()` reflected VM uptime smaller than the maintain interval (60 min).
- Daemon loop: sync/maintain cycles produced zero log output, making `lerim logs` appear idle. Added per-cycle status logging.
- Session queue: NULL `repo_path` jobs clogged the claim queue, preventing valid sessions from being extracted. Added filter in `claim_session_jobs` and guard in `enqueue_session_job`.
- DB migration: orphaned NULL `repo_path` pending/failed jobs are now purged on schema init.
- Explorer subagent: switched from structured `ExplorerEnvelope` output to plain `str` to avoid repeated output-validation failures with models that return empty responses after tool calls.
- Explorer failures no longer crash the lead agent; the `explore` tool returns empty evidence and logs a warning.
- Maintain action path validation: handle list-valued `source_path`/`target_path` from LLM output (model sometimes returns multiple paths per action).
- `run_maintain_once` now accepts a `trigger` parameter instead of hardcoding `"manual"` for all service-run records.

## [0.1.5] - 2026-03-01

### Added

- Per-run LLM cost tracking via OpenRouter's `usage.cost` response field. PydanticAI calls captured via httpx response hook; DSPy calls captured from LM history. Cost (USD) is logged in `activity.log` and returned in sync/maintain/ask result payloads.
- `cost_usd` field on `SyncResultContract`, `MaintainResultContract`, and `api_ask` response.
- Activity log format now includes cost column: `timestamp | op | project | stats | $cost | duration`.
- Chronological (oldest-first) session processing — adapters sort sessions by `start_time`, `claim_session_jobs` orders ASC, `_process_claimed_jobs` runs sequentially so later sessions correctly update earlier memories.
- Maintain prompt instructs chronological memory processing (oldest `created` first).

### Changed

- Replaced raw markdown `write` tool for memory files with structured `write_memory` tool. LLM passes fields (`primitive`, `title`, `body`, `confidence`, `tags`, `kind`); Python builds the markdown via `MemoryRecord.to_markdown()`. Eliminates frontmatter format errors from LLM non-determinism.
- `write_file_tool` now raises `ModelRetry` for memory primitive paths (decisions/learnings), directing the LLM to use `write_memory` instead. Still accepts non-memory writes (JSON artifacts, reports, archived copies).
- Restructured sync prompt into numbered steps with explicit batching instructions (parallel pipeline calls, parallel explores, parallel write_memory calls, parallel report writes).
- Updated maintain prompt to use `write_memory` for consolidation and `write` only for archived copies and reports.
- `_process_claimed_jobs` now runs sequentially (was parallel via `ThreadPoolExecutor`) to ensure chronological memory consistency.
- Removed `_normalize_memory_write` (dead code after `write_memory` tool).
- Removed `memory_write_schema_prompt()` (no longer needed — LLM doesn't write frontmatter).

### Fixed

- `lerim down` now checks if the container is actually running before attempting to stop it. Reports three states: "not running", "stopped", or "cleaned up stale containers."
- Docker restart policy changed from `unless-stopped` to `"no"` — container no longer auto-restarts after reboots, preventing silent LLM API costs.

### Infrastructure

- Added `pytest-xdist` for parallel LLM-bound test execution. Smoke, integration, and e2e tests run with `-n auto` (~2x speedup for e2e: 10min to 5min).
- Test runner defaults updated to match `default.toml` models (`MiniMax-M2.5` all roles, with ZAI coding plan fallbacks).

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
- Memory extraction pipeline using DSPy ChainOfThought with transcript windowing to extract decisions and learnings from coding session traces.
- Trace summarization pipeline using DSPy ChainOfThought with transcript windowing to produce structured summaries with YAML frontmatter.
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
