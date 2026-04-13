# Lerim Test Suite

## Quick Reference

```bash
tests/run_tests.sh unit          # No LLM, ~3s
tests/run_tests.sh smoke         # Real LLM, ~1min
tests/run_tests.sh integration   # Real LLM, ~5min
tests/run_tests.sh e2e           # Real LLM, ~10min
tests/run_tests.sh all           # All of the above + lint + quality
```

Override the test LLM: `LERIM_TEST_PROVIDER=openrouter LERIM_TEST_MODEL=openai/gpt-5-nano tests/run_tests.sh smoke`

## Test Tiers

### Unit (`tests/unit/`, ~1189 tests)

Fast, deterministic, no LLM, no network. Covers session adapters (Claude, Codex, Cursor, OpenCode), memory layout and storage, config loading and merging, CLI parsing, dashboard API helpers, memory tool-function boundary checks, provider construction, cost tracking, job queue, transcript parsing, and regression contracts for public API surfaces.

### Smoke (`tests/smoke/test_agent_smoke.py`, 4 tests)

Quick LLM sanity checks for maintain and ask. Verifies `run_maintain(...)` runs on seeded and empty stores without crashing and `run_ask(...)` answers questions or reports no memories. Sync (extract) is no longer covered here — e2e and the eval harness cover it. Gate: `LERIM_SMOKE=1`.

### Integration (`tests/integration/`, 11 tests)

Real LLM calls testing pipeline quality across multiple components. Files: `test_extraction_quality.py` (runs `run_extraction` against fixture traces), `test_maintain_quality.py`, `test_ask_quality.py`. Covers extraction output quality (schema conformance, type classification, minimum recall), maintain quality (dedup detection, staleness handling, index consistency), and ask quality (answer relevance, memory citation). Gate: `LERIM_INTEGRATION=1`.

### E2E (`tests/e2e/`, ~7 tests)

Full agent flows as a user would invoke them. Files: `test_sync_flow.py`, `test_maintain_flow.py`, `test_full_cycle.py`, `test_context_layers.py`. Covers sync (trace -> extract + memory write + summary + index update), maintain on seeded memory, full reset -> sync -> ask cycle, and ask context forwarding. Gate: `LERIM_E2E=1`.

## Architecture Under Test

- **Sync**: PydanticAI single-pass extraction agent `run_extraction(memory_root, trace_path, model, run_folder)` — one `pydantic_ai.Agent` with 8 tools (read/grep/scan/note/prune/write/edit/verify_index) and 3 history processors (context pressure, notes state, prune rewriter). Request budget auto-scales from trace size via `compute_request_budget`.
- **Agents**: `run_extraction`, `run_maintain`, and `run_ask` are PydanticAI flows using the same model role.
- **Tools**: module-level tool functions in `lerim.agents.tools` (`read`, `grep`, `scan`, `write`, `edit`, `archive`, `verify_index`, plus `note`/`prune` for extraction state control).
- **Config**: single `[roles.agent]` role (no separate extract_role).
- **Memory**: 3-field frontmatter (`name`, `description`, `type`), `index.md`, `summaries/` with date-prefixed files.
- **No** explorer subagent, no windowing pipeline.

## CI/CD

Only **unit tests + lint** run in GitHub Actions (`.github/workflows/ci.yml`). No LLM calls, no API keys needed. Smoke, integration, and e2e are local-only since they require real API keys.

## Environment Variables

| Variable | Purpose |
|----------|---------|
| `LERIM_SMOKE=1` | Enable smoke tests |
| `LERIM_INTEGRATION=1` | Enable integration tests |
| `LERIM_E2E=1` | Enable e2e tests |
| `LERIM_TEST_PROVIDER` | Override LLM provider for all test roles |
| `LERIM_TEST_MODEL` | Override LLM model for all test roles |

## Adding New Tests

- Unit tests go in `tests/unit/test_<name>.py` -- no marker needed, always run.
- Smoke/integration/e2e go in the appropriate directory -- the conftest skip gate handles gating.
- Each test file needs a docstring explaining what it tests.
- Integration tests can use `retry_on_llm_flake` from `tests/integration/conftest.py` for non-deterministic LLM output.

## Fixtures

Defined in `tests/conftest.py`:

| Fixture | Description |
|---------|-------------|
| `tmp_lerim_root` | Temporary Lerim data root with `memory/`, `workspace/`, `index/` subdirs |
| `tmp_config` | Config object pointing at `tmp_lerim_root` |
| `seeded_memory` | `tmp_lerim_root` with fixture memory files copied into `memory/` |

Fixture data in `tests/fixtures/`:

- `traces/` -- JSONL session traces for all adapters (Claude, Codex, Cursor, OpenCode) plus edge cases
- `memories/` -- sample memory files (decisions, learnings, duplicates, stale entries)
