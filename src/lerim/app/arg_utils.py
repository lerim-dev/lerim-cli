"""Small argument parsing helpers shared by CLI and daemon commands."""

from __future__ import annotations


def parse_duration_to_seconds(raw: str) -> int:
    """Parse ``<number><unit>`` durations like ``30s`` or ``7d`` to seconds."""
    value = (raw or "").strip().lower()
    if len(value) < 2:
        raise ValueError("duration must be <number><unit>, for example: 30s, 2m, 1h, 7d")
    unit = value[-1]
    amount_text = value[:-1]
    if not amount_text.isdigit():
        raise ValueError("duration must be <number><unit>, for example: 30s, 2m, 1h, 7d")
    amount = int(amount_text)
    if amount <= 0:
        raise ValueError("duration must be greater than 0")
    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    if unit not in multipliers:
        raise ValueError("duration unit must be one of: s, m, h, d")
    return amount * multipliers[unit]


def parse_csv(raw: str | None) -> list[str]:
    """Split a comma-delimited string into trimmed non-empty values."""
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def parse_agent_filter(raw: str | None) -> list[str] | None:
    """Normalize agent filter input and drop the ``all`` sentinel."""
    values = parse_csv(raw)
    cleaned = [value for value in values if value and value != "all"]
    if not cleaned:
        return None
    return sorted(set(cleaned))


if __name__ == "__main__":
    """Run a real-path smoke test for argument parsing helpers."""
    assert parse_duration_to_seconds("30s") == 30
    assert parse_duration_to_seconds("2m") == 120
    assert parse_duration_to_seconds("1h") == 3600
    assert parse_duration_to_seconds("1d") == 86400
    assert parse_csv(" codex, claude ,, opencode ") == ["codex", "claude", "opencode"]
    assert parse_agent_filter("all,codex,codex,claude") == ["claude", "codex"]
    assert parse_agent_filter("all") is None
