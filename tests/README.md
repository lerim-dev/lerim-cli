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

## Directory Structure

```
tests/
  conftest.py              # Root: shared fixtures, marker registration
  helpers.py               # make_config, run_cli, etc.
  run_tests.sh             # Directory-based test selection
  test_config.toml         # Default LLM config for smoke/integration/e2e
  js_render_harness.js     # JavaScript rendering test
  fixtures/                # Shared across tiers
    traces/                # JSONL session traces
    memories/              # Seeded memory files
    cline/                 # Cline adapter test data
  unit/                    # Flat, descriptive names. No LLM, <5s.
    conftest.py            # autouse dummy API key
  smoke/                   # Quick LLM sanity, requires LERIM_SMOKE=1
    conftest.py            # Skip gate
  integration/             # Real LLM, quality checks, requires LERIM_INTEGRATION=1
    conftest.py            # Skip gate
  e2e/                     # Full CLI flows, requires LERIM_E2E=1
    conftest.py            # Skip gate
```

Test selection is **directory-based**: `pytest tests/unit/` runs only unit tests. No `--ignore` flags or marker filtering needed.

## Test Categories

### Unit (`pytest tests/unit/`)

Fast, deterministic tests with no LLM calls and no network. External state (config paths, DB paths) is monkeypatched to temp directories.

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
| `test_settings.py` | Settings coverage gaps: `load_toml_file`, `_expand`, `_to_fallback_models`, `_parse_string_table`, `save_config_patch`, layer precedence |
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
| `test_memory_repo.py` | Memory path helpers: `build_memory_paths`, `ensure_memory_paths`, `reset_memory_root` |
| `test_access_tracker.py` | Memory access tracking: `init_access_db`, `record_access`, `get_access_stats`, `is_body_read`, `extract_memory_id` |
| `test_queue.py` | Queue facade verification: re-export completeness, identity with catalog originals |
| `test_logging.py` | Logger configuration |
| `test_skills.py` | Skill file discovery |
| `test_indexer_platform_paths.py` | Platform path resolution for indexing |
| `test_extract_lead_authority.py` | Lead agent is sole write authority |
| `test_extract_parser_boundary.py` | Extraction parser boundary enforcement |
| `test_session_extract_writeback.py` | Session extraction writeback to catalog |
| `test_daemon_sync_maintain.py` | Daemon loop scheduling: independent sync/maintain intervals, config fields |
| `test_maintain_command.py` | Maintain CLI command routing |
| `test_learning_runs.py` | Learning run tracking |
| `test_agent_memory_write_flow.py` | Agent memory write flow (unit-level) |
| `test_dashboard_read_only_contract.py` | Dashboard endpoints are read-only |
| `test_dashboard_visual_polish.py` | Dashboard HTML rendering |
| `test_graph_explorer_frontend.py` | Graph explorer frontend rendering |
| `test_index_html.py` | Dashboard index.html serving |
| `test_trace_summarization_pipeline.py` | Trace summarization pipeline contracts |
| `test_eval_scores.py` | Eval scoring utilities, schema checks, judge output parsing, prompt building |

### Smoke (`pytest tests/smoke/`)

Quick LLM sanity checks. Skipped unless `LERIM_SMOKE=1` is set. Default provider: `openrouter/qwen/qwen3-coder-30b-a3b-instruct`.

| File | What it tests |
|------|---------------|
| `test_pipelines.py` | DSPy LM configuration for extract/summarize roles; extraction and summarization pipelines produce output against fixture traces |
| `test_agent.py` | PydanticAI agent returns a response for a simple question |

### Integration (`pytest tests/integration/`)

Multi-component flows with real LLM calls, real file I/O, and real DB writes. Skipped unless `LERIM_INTEGRATION=1` is set.

| File | What it tests |
|------|---------------|
| `test_extract.py` | Feed fixture JSONL traces through DSPy extraction pipeline; verify valid `MemoryRecord` output |
| `test_summarize.py` | Feed seeded memory directories through summarization pipeline; verify valid summary markdown files |
| `test_agent.py` | Full PydanticAI agent ask with memory context |
| `test_providers.py` | LM provider construction works with actual configured backend |
| `test_memory_write.py` | Agent-driven memory write flows with real LLM |

### E2E (`pytest tests/e2e/`)

Full CLI command flows as a user would invoke them. Skipped unless `LERIM_E2E=1` is set.

| File | What it tests |
|------|---------------|
| `test_sync.py` | `lerim sync` against fixture traces creates memories; re-running is idempotent |
| `test_maintain.py` | `lerim maintain` on seeded memories performs maintenance actions |
| `test_full_cycle.py` | Full lifecycle: reset -> sync -> ask |
| `test_real.py` | Real-world e2e with actual connected platforms |
| `test_context_layers.py` | Context layer resolution end-to-end |
| `test_memory_write_modes.py` | Agent memory write modes end-to-end |

## Regression / Contract Tests

Regression tests live in unit-land (no LLM needed) but pin down public API surfaces. If a field is added or removed from a Pydantic model, or a CLI subcommand is renamed, these tests fail.

`test_regression_contracts.py` checks:
- **`SyncResultContract`** — exact field set
- **`MaintainResultContract`** — exact field set
- **`MemoryCandidate`** — required fields
- **CLI subcommands** — all present
- **`MEMORY_FRONTMATTER_SCHEMA`** — expected keys per type

## Fixture Dataset

Hand-crafted fixture files live in `tests/fixtures/`. These are NOT auto-generated — they are minimal, deterministic inputs designed for specific test scenarios.

### Trace fixtures (`fixtures/traces/`)

| File | Lines | Format | Purpose |
|------|-------|--------|---------|
| `claude_simple.jsonl` | 6 | Claude | JWT auth decision + CORS learning; primary happy-path trace |
| `claude_long_multitopic.jsonl` | ~20 | Claude | Long multi-topic session; tests windowed extraction |
| `codex_simple.jsonl` | varies | Codex | Codex adapter parsing verification |
| `codex_with_tools.jsonl` | varies | Codex | Codex trace with tool calls; tests tool-call extraction |
| `debug_session.jsonl` | ~10 | Generic | Debugging session; tests friction/pitfall extraction |
| `mixed_decisions_learnings.jsonl` | 8 | Generic | Multiple decisions AND learnings in one trace |
| `edge_short.jsonl` | 2 | Generic | Minimal conversation; edge case for very short input |
| `edge_empty.jsonl` | 2 | Generic | Empty user content; edge case for noise/empty input handling |

### Memory fixtures (`fixtures/memories/`)

| File | Primitive | Purpose |
|------|-----------|---------|
| `decision_auth_pattern.md` | decision | JWT/HS256 auth decision with full frontmatter |
| `learning_queue_fix.md` | learning | Atomic queue operations learning |
| `learning_stale.md` | learning | Old (2025), low-confidence (0.3) record; tests archival/decay |
| `learning_duplicate_a.md` | learning | Near-duplicate A; tests deduplication |
| `learning_duplicate_b.md` | learning | Near-duplicate B; tests deduplication |

## Shared Infrastructure

### `conftest.py` (root)

Shared pytest fixtures available to all test tiers:
- **`tmp_lerim_root`** — Temporary directory with canonical Lerim folder structure
- **`tmp_config`** — `Config` object pointing at `tmp_lerim_root`
- **`seeded_memory`** — `tmp_lerim_root` with fixture memory files
- **`skip_unless_env(var)`** — Marker helper
- **LLM config auto-apply** — Detects smoke/integration/e2e tests and sets `LERIM_CONFIG`

### Tier-specific `conftest.py`

- **`unit/conftest.py`** — Autouse dummy API key for PydanticAI constructors
- **`smoke/conftest.py`** — Skip all unless `LERIM_SMOKE=1`
- **`integration/conftest.py`** — Skip all unless `LERIM_INTEGRATION=1`
- **`e2e/conftest.py`** — Skip all unless `LERIM_E2E=1`

### `helpers.py`

- **`make_config(base)`** — Builds a deterministic `Config` rooted at a given path
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

- Place unit tests in `tests/unit/test_<name>.py` — no marker needed.
- For smoke/integration/e2e, place in the appropriate directory with the `pytestmark` marker.
- Add new fixture files to `tests/fixtures/` as needed.
- Each test file must have a docstring at the top explaining what it tests.
- Each test function should test ONE thing.
- Update this README when adding new test files or changing test infrastructure.

## DSPy Thread Safety

PydanticAI dispatches tool functions to worker threads. DSPy's `dspy.configure(lm=lm)` is **not thread-safe**. The pipelines use `dspy.context(lm=lm)` (thread-local context manager) instead. See `extract_pipeline.py` and `summarization_pipeline.py`.
