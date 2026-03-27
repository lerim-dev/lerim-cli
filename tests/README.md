# Lerim Test Suite

## Quick Reference

```bash
tests/run_tests.sh unit          # No LLM, ~3s
tests/run_tests.sh smoke         # Real LLM, ~1min
tests/run_tests.sh integration   # Real LLM, ~5min
tests/run_tests.sh e2e           # Real LLM, ~10min
tests/run_tests.sh all           # All of the above + lint + quality
```

Override the test LLM: `LERIM_TEST_PROVIDER=minimax LERIM_TEST_MODEL=MiniMax-M2.5 tests/run_tests.sh integration`

## Test Tiers

### Unit (`tests/unit/`, ~455 tests)

Fast, deterministic, no LLM, no network. Covers session adapters (Claude, Codex, Cursor, OpenCode), memory schemas and storage, config loading and merging, CLI parsing, `dashboard.py` API helpers, runtime tool boundaries, provider construction, cost tracking, job queue, decay logic, and regression contracts for public API surfaces.

### Smoke (`tests/smoke/`, ~5 tests)

Quick LLM sanity checks. Verifies the agent responds, extraction and summarization pipelines produce output, and DSPy LM configures correctly for each role. Gate: `LERIM_SMOKE=1`.

### Integration (`tests/integration/`, ~61 tests)

Real LLM calls testing pipeline quality and multi-component flows. Covers extraction output quality (schema conformance, primitive classification, minimum recall), summarization quality (field presence, word limits, tag relevance, agent detection), DSPy adapter parametrized tests (Chat/JSON/XML × Predict/ChainOfThought), eval runners, judge output parsing, provider fallback, and agent memory-write flows. Gate: `LERIM_INTEGRATION=1`. The 2 Claude CLI judge tests additionally require `LERIM_JUDGE=1`.

### E2E (`tests/e2e/`, ~8 tests)

Full agent flows as a user would invoke them. Covers sync (trace → extract + summarize + memory write), sync idempotency (second run doesn't duplicate), maintain on seeded memory, full reset → sync → ask cycle, and ask end-to-end. Gate: `LERIM_E2E=1`.

## CI/CD

Only **unit tests + lint** run in GitHub Actions (`.github/workflows/ci.yml`). No LLM calls, no API keys needed. Smoke, integration, and e2e are local-only since they require real API keys.

## Environment Variables

| Variable | Purpose |
|----------|---------|
| `LERIM_TEST_PROVIDER` | Override LLM provider for all test roles |
| `LERIM_TEST_MODEL` | Override LLM model for all test roles |
| `LERIM_SMOKE=1` | Enable smoke tests |
| `LERIM_INTEGRATION=1` | Enable integration tests |
| `LERIM_E2E=1` | Enable e2e tests |
| `LERIM_JUDGE=1` | Enable Claude CLI judge integration tests |

## Adding New Tests

- Unit tests go in `tests/unit/test_<name>.py` — no marker needed, always run.
- Smoke/integration/e2e go in the appropriate directory — the conftest skip gate handles gating.
- Each test file needs a docstring explaining what it tests.

## DSPy Thread Safety

Pipelines use `dspy.context(lm=lm)` (thread-local) instead of `dspy.configure()` (global). See `call_with_fallback` in `memory/utils.py`.
