"""Unit tests for shared adapter helpers in lerim.adapters.common."""

from __future__ import annotations

from datetime import datetime, timezone

from lerim.adapters.common import (
    count_non_empty_files,
    in_window,
    load_jsonl_dict_lines,
    parse_timestamp,
)


def test_parse_timestamp_iso():
    """ISO 8601 string -> datetime."""
    result = parse_timestamp("2026-02-20T10:00:00+00:00")
    assert isinstance(result, datetime)
    assert result.year == 2026
    assert result.tzinfo is not None


def test_parse_timestamp_epoch_ms():
    """Millisecond epoch int -> datetime."""
    result = parse_timestamp(1_706_000_000_000)
    assert isinstance(result, datetime)
    assert result.tzinfo is not None


def test_parse_timestamp_epoch_s():
    """Second epoch int -> datetime."""
    result = parse_timestamp(1_706_000_000)
    assert isinstance(result, datetime)
    assert result.tzinfo is not None


def test_parse_timestamp_invalid():
    """Invalid input -> None (no crash)."""
    assert parse_timestamp("not-a-date") is None
    assert parse_timestamp(None) is None
    assert parse_timestamp("") is None
    assert parse_timestamp([1, 2, 3]) is None


def test_load_jsonl_dict_lines_valid(tmp_path):
    """File with valid JSON dict lines -> list of dicts."""
    f = tmp_path / "valid.jsonl"
    f.write_text('{"a":1}\n{"b":2}\n', encoding="utf-8")
    rows = load_jsonl_dict_lines(f)
    assert rows == [{"a": 1}, {"b": 2}]


def test_load_jsonl_dict_lines_mixed(tmp_path):
    """File with dicts + arrays + invalid -> only dicts returned."""
    f = tmp_path / "mixed.jsonl"
    f.write_text('{"a":1}\n[1,2,3]\nnot-json\n{"b":2}\n', encoding="utf-8")
    rows = load_jsonl_dict_lines(f)
    assert rows == [{"a": 1}, {"b": 2}]


def test_load_jsonl_dict_lines_empty(tmp_path):
    """Empty file -> empty list."""
    f = tmp_path / "empty.jsonl"
    f.write_text("", encoding="utf-8")
    assert load_jsonl_dict_lines(f) == []


def test_count_non_empty_files(tmp_path):
    """Count files matching glob that have content."""
    (tmp_path / "a.jsonl").write_text('{"x":1}', encoding="utf-8")
    (tmp_path / "b.jsonl").write_text("", encoding="utf-8")  # empty
    (tmp_path / "c.txt").write_text("data", encoding="utf-8")  # wrong ext
    assert count_non_empty_files(tmp_path, "*.jsonl") == 1


def test_in_window_inside():
    """Datetime within start-end -> True."""
    now = datetime(2026, 2, 20, 12, 0, 0, tzinfo=timezone.utc)
    start = datetime(2026, 2, 20, 0, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 2, 21, 0, 0, 0, tzinfo=timezone.utc)
    assert in_window(now, start, end) is True


def test_in_window_outside():
    """Datetime outside range -> False."""
    now = datetime(2026, 2, 22, 12, 0, 0, tzinfo=timezone.utc)
    start = datetime(2026, 2, 20, 0, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 2, 21, 0, 0, 0, tzinfo=timezone.utc)
    assert in_window(now, start, end) is False


def test_in_window_none_bounds():
    """None start or end means unbounded."""
    now = datetime(2026, 2, 20, 12, 0, 0, tzinfo=timezone.utc)
    assert in_window(now, None, None) is True
    assert in_window(now, None, datetime(2027, 1, 1, tzinfo=timezone.utc)) is True
    assert in_window(now, datetime(2025, 1, 1, tzinfo=timezone.utc), None) is True
