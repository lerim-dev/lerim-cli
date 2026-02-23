# Linting

Summary.
Linting is static checks. It enforces simple rules before tests.
This repo uses Ruff for Python only.

Enforced rules.
- Syntax errors, undefined names, unused imports (`E4`, `E7`, `E9`, `F`).
- Mutable default arguments (`B006`).
- No `print` in code (`T201`).

Output.
- CLI uses `_emit(...)` to write to stdout/stderr.
- Services use `logger`.

Advisory rules (agents).
- Error handling: no bare `except`. Raise typed errors.
- API boundaries: avoid wildcard exports.
- Testing: be explicit about async behavior.
- Control flow: avoid deep nesting; use early returns.
- Arguments: avoid boolean positional args; use named args.

How to use.
Install lint deps:
`uv pip install -e ".[lint]"`

Run lint:
`scripts/run_tests.sh lint`

Notes.
This is baseline only. Keep changes small.
If lint is too strict, add a narrow ignore with a short reason.
