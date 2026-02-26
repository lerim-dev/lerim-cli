# Contributing

Lerim is licensed under BSL 1.1. By contributing you agree your changes fall under the same license (1 user free, 2+ users need a commercial license).

## Dev environment setup

Requires Python 3.12+.

```bash
uv venv && source .venv/bin/activate
uv pip install -e '.[test]'
```

## Running tests

```bash
# Unit tests (no LLM keys needed)
tests/run_tests.sh unit

# Smoke tests (needs LLM API key in env)
tests/run_tests.sh smoke

# Everything
tests/run_tests.sh all
```

Lint before submitting:

```bash
ruff check src/ tests/
```

## Coding style

Full rules live in the [coding rules document](https://github.com/lerim-dev/lerim-cli/blob/main/docs/simple-coding-rules.md). The short version:

- **Minimal code.** Prefer fewer functions, fewer layers, fewer lines.
- **Strict schemas.** Use Pydantic models / enums for inputs and outputs.
- **Fail fast.** No `try/except` fallbacks for missing packages. If something is required, let it raise.
- **Docstrings everywhere.** Every file gets a top-level docstring. Every function gets a docstring.
- **Self-tests.** Add an `if __name__ == "__main__":` block that exercises the real code path (no mocking or stubbing). Keep it inline, not in a separate function.
- **No dead code.** When you replace logic, remove the old path.
- **Config from TOML, keys from env.** Runtime config comes from the TOML layer stack. Only API keys use environment variables.

## Adding a new platform adapter

This is the most common contribution. Follow these steps:

1. **Create `src/lerim/adapters/<platform>.py`.**
   Start with a top-level docstring. Implement the functions required by the `Adapter` protocol in `src/lerim/adapters/base.py`:

    - `default_path() -> Path | None` — where traces live on disk
    - `count_sessions(path) -> int`
    - `iter_sessions(traces_dir, start, end, known_run_ids) -> list[SessionRecord]`
    - `find_session_path(session_id, traces_dir) -> Path | None`
    - `read_session(session_path, session_id) -> ViewerSession | None`

    See an existing adapter (e.g. `codex.py` or `claude.py`) as a reference.

2. **Register the adapter** in `src/lerim/adapters/registry.py`: add an entry to `_ADAPTER_MODULES` and optionally to `_AUTO_SEED_PLATFORMS`.

3. **Add a self-test** (`if __name__ == "__main__":` block) at the bottom of the new adapter file.

4. **Add unit tests** in `tests/test_<platform>_adapter.py`.

5. **Update `tests/README.md`** if you added new fixtures or test infrastructure.

## Reporting bugs

Open a [GitHub issue](https://github.com/lerim-dev/lerim-cli/issues) with:

- Steps to reproduce
- Expected vs actual behavior
- Lerim version (`lerim --version`), Python version, and OS
- Relevant config (redact API keys)

## Pull request checklist

- [ ] `ruff check src/ tests/` passes with no errors
- [ ] `tests/run_tests.sh unit` passes
- [ ] New/changed files have top-level docstrings and function docstrings
- [ ] New source files include an `if __name__ == "__main__":` self-test when practical
- [ ] No mocking or stubbing in self-test flows
- [ ] Related docs updated if behavior changed
