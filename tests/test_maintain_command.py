"""CLI maintain-command behavior tests."""

from __future__ import annotations

from tests.helpers import run_cli_json


def test_maintain_dry_run() -> None:
    """Dry-run maintain returns immediately with dry_run flag."""
    code, payload = run_cli_json(["maintain", "--dry-run", "--json"])
    assert code == 0
    assert payload["dry_run"] is True
