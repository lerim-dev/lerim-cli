"""Shared adapter helpers for timestamps, JSONL loading, window filtering, and hashing."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any


def parse_timestamp(value: Any) -> datetime | None:
    """Parse many timestamp shapes into a timezone-aware UTC datetime."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)):
        timestamp = float(value)
        if abs(timestamp) > 1e10:
            timestamp /= 1000.0
        try:
            parsed = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        except (ValueError, OSError):
            return None
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def load_jsonl_dict_lines(path: Path) -> list[dict[str, Any]]:
    """Read a JSONL file and return only dict payload rows."""
    entries: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    entries.append(payload)
    except OSError:
        return []
    return entries


def count_non_empty_files(path: Path, pattern: str) -> int:
    """Count non-empty files under ``path`` matching a glob pattern."""
    if not path.exists():
        return 0
    count = 0
    for file_path in path.rglob(pattern):
        try:
            if file_path.is_file() and file_path.stat().st_size > 0:
                count += 1
        except OSError:
            continue
    return count


def in_window(
    value: datetime | None, start: datetime | None, end: datetime | None
) -> bool:
    """Return whether ``value`` is inside the inclusive ``start``/``end`` window."""
    if value is None:
        return start is None and end is None
    if start and value < start:
        return False
    if end and value > end:
        return False
    return True


def compute_file_hash(path: Path) -> str:
    """Compute SHA-256 hex digest of a file's raw bytes."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Canonical compacted schema
# ---------------------------------------------------------------------------
# Every adapter must produce JSONL entries matching this shape:
#   {"type": "user|assistant", "message": {"role": "user|assistant", "content": ...}, "timestamp": "..."}
# Content can be a string or a list of block dicts (e.g. tool_use, tool_result).

_CANONICAL_TYPES = frozenset({"user", "assistant"})


def make_canonical_entry(
    entry_type: str,
    role: str,
    content: str | list,
    timestamp: str | None,
) -> dict[str, Any]:
    """Build a canonical compacted JSONL entry."""
    return {
        "type": entry_type,
        "message": {"role": role, "content": content},
        "timestamp": timestamp,
    }


def validate_canonical_entry(obj: dict[str, Any]) -> bool:
    """Return True if entry conforms to the canonical compacted schema."""
    if set(obj.keys()) != {"type", "message", "timestamp"}:
        return False
    if obj["type"] not in _CANONICAL_TYPES:
        return False
    msg = obj.get("message")
    if not isinstance(msg, dict) or set(msg.keys()) != {"role", "content"}:
        return False
    if msg["role"] not in _CANONICAL_TYPES:
        return False
    if not isinstance(msg["content"], (str, list)):
        return False
    return True


def normalize_timestamp_iso(value: Any) -> str | None:
    """Parse any timestamp shape and return ISO 8601 UTC string, or None."""
    dt = parse_timestamp(value)
    if dt is None:
        return None
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def compact_jsonl(
    raw_text: str, cleaner: Callable[[dict[str, Any]], dict[str, Any] | None]
) -> str:
    """Compact JSONL text by applying a format-specific cleaner to each parsed line.

    The *cleaner* receives each parsed JSON dict and returns the cleaned dict,
    or ``None`` to drop the line entirely.  Non-JSON lines are kept as-is.
    """
    kept: list[str] = []
    for line in raw_text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            kept.append(line)
            continue
        obj = cleaner(obj)
        if obj is None:
            continue
        kept.append(json.dumps(obj, ensure_ascii=False))
    return "\n".join(kept) + "\n"


def readonly_connect(db_path: Path) -> sqlite3.Connection:
    """Open a read-only SQLite connection with Row factory."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def write_session_cache(
    cache_dir: Path,
    run_id: str,
    lines: list[str],
    compact_fn: Callable[[str], str],
) -> Path:
    """Write compacted session JSONL to cache directory and return the path."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{run_id}.jsonl"
    raw = "\n".join(lines) + "\n"
    cache_path.write_text(compact_fn(raw), encoding="utf-8")
    return cache_path


if __name__ == "__main__":
    """Run a real-path smoke test for timestamp parsing and JSONL reading."""
    assert parse_timestamp("2026-02-19T10:00:00+00:00") is not None
    assert parse_timestamp(1_706_000_000) is not None
    assert parse_timestamp("not-a-date") is None

    with TemporaryDirectory() as tmp_dir:
        sample = Path(tmp_dir) / "sample.jsonl"
        sample.write_text('{"a":1}\n{"b":2}\nnot-json\n[1,2,3]\n', encoding="utf-8")
        rows = load_jsonl_dict_lines(sample)
        assert rows == [{"a": 1}, {"b": 2}]
        assert count_non_empty_files(Path(tmp_dir), "*.jsonl") == 1

        h1 = compute_file_hash(sample)
        assert len(h1) == 64, "SHA-256 hex digest should be 64 chars"
        h2 = compute_file_hash(sample)
        assert h1 == h2, "Hash should be deterministic"
        sample.write_text('{"c":3}\n', encoding="utf-8")
        h3 = compute_file_hash(sample)
        assert h3 != h1, "Changed file should produce different hash"

    now = datetime.now(timezone.utc)
    assert in_window(now, now, now)
