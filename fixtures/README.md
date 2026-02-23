# Lerim Golden Fixtures

This directory contains golden trace fixtures for validation and regression testing.

## Structure

```
fixtures/
├── README.md           # This file
├── claude/             # Claude Code connector fixtures
│   └── coding-run.json
├── codex/              # Codex connector fixtures
│   └── coding-run.json
└── opencode/           # OpenCode connector fixtures
    └── coding-run.json
```

## Fixture Requirements

All fixtures must:

1. **Match adapter expectations**: Parse cleanly through the target adapter
2. **Be redacted**: No secrets, PII, or sensitive data
3. **Be minimal**: 1-3 steps each for quick testing
4. **Be realistic**: Represent actual agent behavior patterns

## Usage

### Parse smoke checks (current runtime)

```bash
uv run python - <<'PY'
from pathlib import Path
from lerim.adapters.registry import get_adapter

base = Path("fixtures")
for platform in ("claude", "codex", "opencode"):
    adapter = get_adapter(platform)
    fixture = base / platform / "coding-run.json"
    session = adapter.read_session(fixture, session_id="fixture-smoke")
    print(platform, "messages=", len(session.messages))
PY
```

### Use in tests

```python
from pathlib import Path
import json

FIXTURES_DIR = Path(__file__).parent / "fixtures"

def load_fixture(connector: str, name: str) -> dict:
    path = FIXTURES_DIR / connector / f"{name}.json"
    with open(path) as f:
        return json.load(f)

# Example
run = load_fixture("claude", "coding-run")
```

## Adding New Fixtures

1. Create a JSON file following the Lerim run schema
2. Redact any sensitive information:
   - Replace real paths with placeholders (e.g., `/home/user/project`)
   - Remove API keys, tokens, secrets
   - Anonymize usernames and email addresses
   - Truncate long outputs
3. Validate by parsing with the relevant adapter
4. Add to appropriate connector directory
5. Update tests if needed

## Fixture Categories

### coding-run.json

A basic coding run that:
- Receives a user request
- Makes tool calls (read, write, execute)
- Produces file changes
- Reports usage/costs

### (Future) support-run.json

A support agent run with:
- User question
- Knowledge retrieval
- Response generation

### (Future) research-run.json

A research agent run with:
- Research query
- Web search/API calls
- Content synthesis
