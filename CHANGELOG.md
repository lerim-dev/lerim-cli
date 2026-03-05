# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.60] - 2026-03-05

### Added

- **Ollama lifecycle management**: automatic model load/unload around sync and maintain cycles. Models are warm-loaded into GPU/RAM before each cycle and unloaded after (`keep_alive: 0`) to free 5-10 GB of memory between runs. Controlled by `auto_unload = true` in `[providers]`.
- **LiteLLM proxy support**: new `litellm_proxy` provider base URL in `[providers]` for routing PydanticAI OpenAI-format calls through LiteLLM to Ollama's native API (enables thinking mode control).
- **Eval framework**: four eval pipelines (`extraction`, `summarization`, `sync`, `maintain`) with LLM-as-judge scoring, config-driven model comparison, and `bench_ollama.sh` benchmarking script.
- Eval configs for Ollama models (Qwen3.5 4B/9B, thinking/non-thinking) and MiniMax-M2.5 cloud baseline.
- Synthetic eval traces and judge prompt templates for all four pipelines.
- `evals/compare.py` for cross-config result comparison.
- `lerim skill install` command to copy skill files into agent directories.

### Fixed

- **Docker networking**: generated `docker-compose.yml` now includes `extra_hosts: host.docker.internal:host-gateway` so containers can reach Ollama running on the host.

### Changed

- Evals folder reorganized: active configs moved to `evals/configs/`, stale MLX configs removed.
- Default provider switched to MiniMax-M2.5 with Z.AI fallback.

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

- Per-run LLM cost tracking via OpenRouter's `usage.cost` response field. Cost (USD) logged in `activity.log` and returned in sync/maintain/ask result payloads.
- Chronological (oldest-first) session processing for correct memory ordering.

### Changed

- Structured `write_memory` tool replaces raw markdown writes for memory files.
- `_process_claimed_jobs` runs sequentially (was parallel) for chronological memory consistency.
- Activity log format now includes cost column.

## [0.1.0] - 2026-02-28

### Added

- Docker service architecture: always-on daemon + HTTP API + dashboard in a single container.
- `lerim init` interactive setup wizard for first-time configuration.
- `lerim project add/list/remove` for incremental project registration.
- `lerim up/down/logs` for Docker container lifecycle management.
- `lerim serve` command — combined HTTP API + dashboard + daemon loop (Docker entrypoint, also usable directly without Docker).
- Service commands (`ask`, `sync`, `maintain`, `status`) are thin HTTP clients that talk to the running server.
- HTTP API: `/api/health`, `/api/ask`, `/api/sync`, `/api/maintain`, `/api/status`, `/api/connect`, `/api/project/*`.
- `[agents]`, `[projects]`, and `[providers]` config sections in `config.toml`.
- Provider API base URLs configurable via `[providers]` section (no more hardcoded URLs).
- `Dockerfile` with Python 3.12, health check, `lerim serve` entrypoint.
- Same-path volume mounting for zero path translation between host and container.
- Continual learning layer for coding agents and projects.
- Platform adapters for Claude Code, Codex CLI, Cursor, and OpenCode.
- Memory extraction pipeline using DSPy ChainOfThought with transcript windowing to extract decisions and learnings from coding session traces.
- Trace summarization pipeline using DSPy ChainOfThought with transcript windowing to produce structured summaries with YAML frontmatter.
- PydanticAI lead agent with a read-only explorer subagent for memory operations.
- Three CLI flows: `sync` (extract, summarize, write memories), `maintain` (merge, archive, decay), and `ask` (query memories).
- Daemon mode for continuous sync and maintain loop.
- Local read-only web dashboard with HTTP API.
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
