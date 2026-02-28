# Lerim Test Suite

## Quick Reference

```bash
# Unit tests (no LLM, no network, ~2s)
tests/run_tests.sh unit

# Smoke tests (requires LLM — default: openrouter, ~80s)
tests/run_tests.sh smoke

# Integration tests (requires LLM, ~5 min)
tests/run_tests.sh integration

# E2E tests (requires LLM, full CLI flows, ~3-10 min)
tests/run_tests.sh e2e

# All categories
tests/run_tests.sh all
```

The test runner auto-activates `.venv` if not already active and `cd`s to the project root.
Override the test LLM provider/model via env vars `LERIM_TEST_PROVIDER` and `LERIM_TEST_MODEL`, or via `tests/test_config.toml`.
Default provider: `openrouter/qwen/qwen3-coder-30b-a3b-instruct` for all roles.

## Test Categories

### Unit (`pytest -m "not integration and not e2e and not smoke"`)

Fast, deterministic tests with no LLM calls and no network. External state (config paths, DB paths) is monkeypatched to temp directories. These test individual functions and classes in isolation.

| File | What it tests |
|------|---------------|
| `test_adapter_common.py` | Shared adapter utilities: role normalization, timestamp parsing, metadata extraction |
| `test_claude_adapter.py` | Claude JSONL trace parsing, session discovery, message extraction |
| `test_codex_adapter.py` | Codex trace parsing and session metadata |
| `test_opencode_adapter.py` | Opencode adapter parsing and JSONL export round-trip |
| `test_cursor_adapter.py` | Cursor adapter parsing |
| `test_adapter_registry.py` | Adapter loading, registration, connected-agent discovery |
| `test_memory_record.py` | `MemoryRecord` construction, markdown serialization, frontmatter round-trips |
| `test_memory_schemas.py` | Pydantic schema validation for memory primitives |
| `test_summary_write.py` | Summary file writing to `memory/summaries/YYYYMMDD/HHMMSS/` layout |
| `test_catalog_queries.py` | Session catalog DB: FTS indexing, job queue enqueue/claim/reclaim, pagination, service runs |
| `test_fts.py` | Full-text search queries and ranking |
| `test_config.py` | Settings loading, `_deep_merge`, `_to_int`/`_to_float` validators, role config building |
| `test_project_scope.py` | Project scope resolution (project-first vs global-only) |
| `test_arg_utils.py` | `parse_duration_to_seconds`, CSV/tag parsing, CLI argument utilities |
| `test_runtime_tools.py` | Tool boundary enforcement (read/write/glob/grep within allowed roots) |
| `test_providers.py` | DSPy/PydanticAI LM provider construction, API key resolution |
| `test_subagents.py` | Explorer subagent builder contracts, read-only tool verification |
| `test_cli.py` | Argument parser validation, command routing, `memory list/add/search` |
| `test_dashboard_api.py` | `_compute_stats`, `_build_memory_graph_payload`, dashboard helper functions |
| `test_regression_contracts.py` | Public API surface checks — import paths and function signatures haven't broken |
| `test_runtime_agent_contract.py` | Lead agent contract (typed deps, typed outputs) |
| `test_memory_layout.py` | Canonical memory directory structure |
| `test_memory_search_toggles.py` | Search mode toggles (files/fts/vector/graph) |
| `test_memory_decay.py` | Confidence decay and archive-threshold logic |
| `test_logging.py` | Logger configuration |
| `test_skills.py` | Skill file discovery |
| `test_indexer_platform_paths.py` | Platform path resolution for indexing |
| `test_extract_lead_authority.py` | Lead agent is sole write authority |
| `test_extract_parser_boundary.py` | Extraction parser boundary enforcement |
| `test_session_extract_writeback.py` | Session extraction writeback to catalog |
| `test_daemon_sync_maintain.py` | Daemon loop scheduling |
| `test_maintain_command.py` | Maintain CLI command routing |
| `test_learning_runs.py` | Learning run tracking |
| `test_agent_memory_write_flow.py` | Agent memory write flow (unit-level) |
| `test_dashboard_read_only_contract.py` | Dashboard endpoints are read-only |
| `test_dashboard_visual_polish.py` | Dashboard HTML rendering |
| `test_graph_explorer_frontend.py` | Graph explorer frontend rendering |
| `test_index_html.py` | Dashboard index.html serving |

### Smoke (`LERIM_SMOKE=1 pytest -m smoke`)

Quick LLM sanity checks gated behind `LERIM_SMOKE=1`. Default provider: `openrouter/qwen/qwen3-coder-30b-a3b-instruct` (configurable via `tests/test_config.toml`).

| File | What it tests |
|------|---------------|
| `test_smoke_pipelines.py` | DSPy LM configuration for extract/summarize roles; extraction and summarization pipelines produce output against fixture traces |
| `test_smoke_agent.py` | PydanticAI agent returns a response for a simple question |

### Integration (`LERIM_INTEGRATION=1 pytest -m integration`)

Multi-component flows with real LLM calls, real file I/O, and real DB writes. Each test is scoped to one subsystem. Slow (~14 min total).

| File | What it tests |
|------|---------------|
| `test_integration_extract.py` | Feed fixture JSONL traces through DSPy extraction pipeline; verify valid `MemoryRecord` output, correct primitives, edge-case handling (empty/short/mixed traces) |
| `test_integration_summarize.py` | Feed seeded memory directories through summarization pipeline; verify valid summary markdown files |
| `test_integration_agent.py` | Full PydanticAI agent ask with memory context; agent answers using seeded memories |
| `test_integration_providers.py` | LM provider construction works with actual configured backend |
| `test_agent_memory_write_integration.py` | Agent-driven memory write flows with real LLM |

### E2E (`LERIM_E2E=1 pytest -m e2e`)

Full CLI command flows as a user would invoke them. Requires working LLM.

| File | What it tests |
|------|---------------|
| `test_e2e_sync.py` | `lerim sync` against fixture traces creates memories; re-running is idempotent (no duplicates) |
| `test_e2e_maintain.py` | `lerim maintain` on seeded memories performs maintenance actions (archival, dedup) |
| `test_e2e_full_cycle.py` | Full lifecycle: reset -> sync -> ask; verifies the whole pipeline end-to-end |
| `test_e2e_real.py` | Real-world e2e with actual connected platforms |
| `test_context_layers_e2e.py` | Context layer resolution end-to-end |
| `test_agent_memory_write_modes_e2e.py` | Agent memory write modes end-to-end |

## Regression / Contract Tests

Regression tests live in unit-land (no LLM needed) but serve a distinct purpose: they pin down public API surfaces so accidental breakage is caught immediately. If a field is added or removed from a Pydantic model, or a CLI subcommand is renamed, these tests fail.

`test_regression_contracts.py` checks:

- **`SyncResultContract`** — exact field set (`trace_path`, `memory_root`, `workspace_root`, `run_folder`, `artifacts`, `counts`, `written_memory_paths`, `summary_path`)
- **`MaintainResultContract`** — exact field set (`memory_root`, `workspace_root`, `run_folder`, `artifacts`, `counts`)
- **`SyncCounts`** — fields: `add`, `update`, `no_op`
- **`MaintainCounts`** — fields: `merged`, `archived`, `consolidated`, `decayed`, `unchanged`
- **`MemoryCandidate`** — fields: `primitive`, `kind`, `title`, `body`, `confidence`, `tags`
- **CLI subcommands** — `connect`, `sync`, `maintain`, `daemon`, `ask`, `memory`, `dashboard`, `status` all present
- **`MEMORY_FRONTMATTER_SCHEMA`** — decision and learning types have expected keys (`id`, `kind`, etc.)

When changing any of these contracts, update the corresponding test assertions. These are intentionally strict — a diff in fields means the contract changed and downstream consumers may break.

## Fixture Dataset

Hand-crafted fixture files live in `tests/fixtures/`. These are NOT auto-generated — they are minimal, deterministic inputs designed for specific test scenarios.

### Trace fixtures (`fixtures/traces/`)

| File | Lines | Format | Purpose |
|------|-------|--------|---------|
| `claude_simple.jsonl` | 6 | Claude (`type`/`message`) | JWT auth decision + CORS learning; primary happy-path trace |
| `claude_long_multitopic.jsonl` | ~20 | Claude | Long multi-topic session; tests windowed extraction |
| `codex_simple.jsonl` | varies | Codex | Codex adapter parsing verification |
| `codex_with_tools.jsonl` | varies | Codex | Codex trace with tool calls; tests tool-call extraction |
| `debug_session.jsonl` | ~10 | Generic | Debugging session; tests friction/pitfall extraction |
| `mixed_decisions_learnings.jsonl` | 8 | Generic (`role`/`content`) | Multiple decisions AND learnings in one trace; tests extraction of mixed primitives |
| `edge_short.jsonl` | 2 | Generic | Minimal conversation; edge case for very short input |
| `edge_empty.jsonl` | 2 | Generic | Empty user content; edge case for noise/empty input handling |

### Memory fixtures (`fixtures/memories/`)

| File | Primitive | Purpose |
|------|-----------|---------|
| `decision_auth_pattern.md` | decision | JWT/HS256 auth decision with full frontmatter; used by `seeded_memory` fixture |
| `learning_queue_fix.md` | learning | Atomic queue operations learning; general seeding |
| `learning_stale.md` | learning | Old (2025), low-confidence (0.3) record; tests archival/decay logic |
| `learning_duplicate_a.md` | learning | Near-duplicate A; tests deduplication |
| `learning_duplicate_b.md` | learning | Near-duplicate B; tests deduplication |

There is also a `fixtures/cline/` directory with Cline adapter test data (pre-existing).

## Shared Infrastructure

### `conftest.py`

Shared pytest fixtures available to all tests:

- **`tmp_lerim_root`** — Temporary directory with canonical Lerim folder structure (`memory/decisions`, `memory/learnings`, `memory/summaries`, `memory/archived/*`, `workspace/`, `index/`)
- **`tmp_config`** — `Config` object pointing at `tmp_lerim_root`
- **`seeded_memory`** — `tmp_lerim_root` with fixture memory files copied into the correct subdirectories
- **`skip_unless_env(var)`** — Marker helper that skips unless an env var is set

### `helpers.py`

Shared test utilities:

- **`make_config(base)`** — Builds a deterministic `Config` rooted at a given path. Uses `openrouter`/`qwen/qwen3-coder-30b-a3b-instruct` for all roles (overridable via `LERIM_TEST_PROVIDER`/`LERIM_TEST_MODEL`).
- **`write_test_config(tmp_path, **sections)`** — Writes a TOML config file for CLI integration tests
- **`run_cli(args)`** — Runs a CLI command in-process, returns `(exit_code, stdout)`
- **`run_cli_json(args)`** — Runs a CLI command and parses stdout as JSON

## Environment Variables

| Variable | Required for | Default LLM |
|----------|-------------|-------------|
| `LERIM_SMOKE=1` | Smoke tests | `openrouter/qwen/qwen3-coder-30b-a3b-instruct` |
| `LERIM_INTEGRATION=1` | Integration tests | `openrouter/qwen/qwen3-coder-30b-a3b-instruct` |
| `LERIM_E2E=1` | E2E tests | `openrouter/qwen/qwen3-coder-30b-a3b-instruct` |
| `LERIM_TEST_PROVIDER` | Override provider | `openrouter` |
| `LERIM_TEST_MODEL` | Override model | `qwen/qwen3-coder-30b-a3b-instruct` |
| `LERIM_CONFIG` | Override config path | `tests/test_config.toml` (auto-applied by conftest) |

## Adding New Tests

- Place unit tests in `tests/test_<module>.py` — no special marker needed.
- For smoke/integration/e2e, use the appropriate pytest marker and gate with `skip_unless_env`.
- Add new fixture files to `tests/fixtures/traces/` or `tests/fixtures/memories/` as needed.
- Each test file must have a docstring at the top explaining what it tests.
- Each test function should test ONE thing.
- Update this README if you add new test files, fixtures, or change the test infrastructure.

## DSPy Thread Safety

PydanticAI dispatches tool functions to worker threads via `anyio.to_thread.run_sync()`. DSPy's `dspy.configure(lm=lm)` is **not thread-safe** — it enforces that settings can only be changed by the thread that initially configured them.

The pipelines use `dspy.context(lm=lm)` (a thread-local context manager) instead of `dspy.configure()`. This ensures extract/summarize tools work correctly when called from PydanticAI worker threads. See `extract_pipeline.py` and `summarization_pipeline.py`.
