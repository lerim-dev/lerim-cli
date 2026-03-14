# Contributing

Lerim is open to contributions -- new agent adapters, bug fixes, documentation
improvements, and feature PRs are welcome.

## License

Lerim is licensed under **BSL 1.1** (Business Source License). By contributing
you agree your changes fall under the same license (1 user free, 2+ users need
a commercial license).

## Dev environment setup

### Prerequisites

- Python 3.12+
- [uv](https://github.com/astral-sh/uv) (recommended) or pip

### Install

```bash
uv venv && source .venv/bin/activate
uv pip install -e '.[test]'
```

This installs Lerim in editable mode with all test dependencies (pytest,
pytest-xdist, ruff, etc.).

## Running tests

Tests are organized by tier in subdirectories. Selection is directory-based:

```bash
# Unit tests (no LLM, no network, ~2s)
tests/run_tests.sh unit

# Smoke tests (requires LLM API key, ~40s)
tests/run_tests.sh smoke

# Integration tests (requires LLM API key, ~3 min)
tests/run_tests.sh integration

# E2E tests (requires LLM API key, ~5 min)
tests/run_tests.sh e2e

# All categories
tests/run_tests.sh all
```

!!! info "LLM tests"
    Smoke, integration, and e2e tests require a valid `MINIMAX_API_KEY` (or
    equivalent) and are gated behind environment variables (`LERIM_SMOKE=1`,
    `LERIM_INTEGRATION=1`, `LERIM_E2E=1`).

### Test organization

| Tier | Directory | Gate | LLM required | Description |
|------|-----------|------|:---:|-------------|
| Unit | `tests/unit/` | *(none)* | No | Fast, deterministic, no network |
| Smoke | `tests/smoke/` | `LERIM_SMOKE=1` | Yes | Quick LLM sanity checks |
| Integration | `tests/integration/` | `LERIM_INTEGRATION=1` | Yes | Multi-component flows with real LLM |
| E2E | `tests/e2e/` | `LERIM_E2E=1` | Yes | Full CLI command flows |

Override the test LLM provider/model with `LERIM_TEST_PROVIDER` and
`LERIM_TEST_MODEL`, or via `tests/test_config.toml`.

## Lint

```bash
ruff check src/ tests/
```

!!! warning "Required before PR"
    `ruff check` must pass with no errors before submitting a pull request.

## Coding style

Full rules are in `docs/simple-coding-rules.md` in the repo.
The short version:

| Rule | Description |
|------|-------------|
| **Minimal code** | Prefer fewer functions, fewer layers, fewer lines. |
| **Strict schemas** | Use Pydantic models / enums for inputs and outputs. |
| **Fail fast** | No `try/except` fallbacks for missing packages. If something is required, let it raise. |
| **Docstrings** | Every file gets a top-level docstring. Every function gets a docstring. |
| **Real tests** | Prefer real-path tests over mocked tests. Validate quality, not only counts. |
| **No dead code** | When you replace logic, remove the old path. |
| **Config from TOML** | Runtime config from TOML layers. Only API keys use env vars. |

## Reporting bugs

Open a [GitHub issue](https://github.com/lerim-dev/lerim-cli/issues) with:

- Steps to reproduce
- Expected vs actual behavior
- Lerim version (`lerim --version`), Python version, and OS
- Relevant config (redact API keys)

## PR checklist

- [ ] `ruff check src/ tests/` passes with no errors
- [ ] `tests/run_tests.sh unit` passes
- [ ] New/changed files have top-level docstrings and function docstrings
- [ ] Related docs updated if behavior changed
- [ ] `tests/README.md` updated if new test files or fixtures were added

## What to contribute

<div class="grid cards" markdown>

-   :material-puzzle-outline: **Agent adapters**

    ---

    Add support for a new coding agent platform. See existing adapters in
    `src/lerim/adapters/` as a reference.

-   :material-bug-outline: **Bug fixes**

    ---

    Browse [open issues](https://github.com/lerim-dev/lerim-cli/issues) for
    bugs to fix.

-   :material-file-document-edit-outline: **Documentation**

    ---

    Improve docs, add examples, fix typos.

-   :material-cog-outline: **Features**

    ---

    Check [open issues](https://github.com/lerim-dev/lerim-cli/issues) tagged
    `enhancement` for feature ideas.

</div>
