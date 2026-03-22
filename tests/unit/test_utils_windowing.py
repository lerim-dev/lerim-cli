"""Tests for window_transcript and window_transcript_jsonl in memory/utils.py.

Covers token ratio differences, oversized line handling, prompt headroom,
overlap carry, and regression for plain-text windowing.
"""

from __future__ import annotations

from lerim.memory.utils import window_transcript, window_transcript_jsonl


# ---------------------------------------------------------------------------
# window_transcript (plain text, 3.5 chars/token ratio) — regression tests
# ---------------------------------------------------------------------------


def test_window_transcript_small_fits_one_window():
    """Text smaller than max_chars returns a single window."""
    text = "x" * 100
    assert window_transcript(text, max_tokens=1000, overlap_tokens=100) == [text]


def test_window_transcript_splits_large_text():
    """Large plain text splits into multiple overlapping windows."""
    text = "y" * 10_000
    windows = window_transcript(text, max_tokens=1000, overlap_tokens=100)
    assert len(windows) > 1
    # Each window respects max_chars = 1000 * 7 // 2 = 3500
    for w in windows:
        assert len(w) <= 3500


def test_window_transcript_uses_35_ratio():
    """Plain text windowing uses 3.5 chars/token (unchanged)."""
    max_tokens = 1000
    max_chars = max_tokens * 7 // 2  # 3500
    text = "a" * (max_chars + 1)
    windows = window_transcript(text, max_tokens=max_tokens, overlap_tokens=0)
    assert len(windows) == 2


# ---------------------------------------------------------------------------
# window_transcript_jsonl — JSON ratio (2.5 chars/token)
# ---------------------------------------------------------------------------


def test_jsonl_uses_25_ratio_with_headroom():
    """JSONL windowing uses 2.5 chars/token and subtracts prompt headroom."""
    max_tokens = 10_000
    # effective_tokens = max(10000 - 5000, 5000) = 5000
    # max_chars = 5000 * 5 // 2 = 12500
    expected_max_chars = 12_500
    # Text that fits under this limit -> single window
    text = "\n".join(f'{{"k": {i}}}' for i in range(100))
    assert len(text) < expected_max_chars
    assert len(window_transcript_jsonl(text, max_tokens=max_tokens, overlap_tokens=0)) == 1


def test_jsonl_headroom_reduces_budget():
    """Prompt headroom (5000 tokens) reduces effective char budget."""
    max_tokens = 300_000
    # effective_tokens = 300000 - 5000 = 295000
    # max_chars = 295000 * 5 // 2 = 737500
    effective_max_chars = 737_500
    # Old ratio would give: 300000 * 7 // 2 = 1_050_000
    old_max_chars = 1_050_000
    # Text between new and old limits should now produce multiple windows
    text = "\n".join(["x" * 200] * 4000)  # ~804000 chars
    assert len(text) > effective_max_chars
    assert len(text) < old_max_chars
    windows = window_transcript_jsonl(text, max_tokens=max_tokens, overlap_tokens=0)
    assert len(windows) >= 2


def test_jsonl_small_text_single_window():
    """Small JSONL text returns a single window."""
    lines = [f'{{"id": {i}, "msg": "hello"}}' for i in range(5)]
    text = "\n".join(lines)
    windows = window_transcript_jsonl(text, max_tokens=100_000, overlap_tokens=1000)
    assert windows == [text]


def test_jsonl_no_window_exceeds_max_chars():
    """No JSONL window should exceed the computed max_chars."""
    max_tokens = 10_000
    effective_tokens = max(max_tokens - 5000, max_tokens // 2)
    max_chars = effective_tokens * 5 // 2  # 12500
    lines = [f'{{"data": "{"a" * 500}", "idx": {i}}}' for i in range(100)]
    text = "\n".join(lines)
    windows = window_transcript_jsonl(text, max_tokens=max_tokens, overlap_tokens=500)
    for i, w in enumerate(windows):
        assert len(w) <= max_chars, f"Window {i} has {len(w)} chars, max is {max_chars}"


# ---------------------------------------------------------------------------
# Oversized single JSONL lines
# ---------------------------------------------------------------------------


def test_jsonl_oversized_line_gets_split():
    """A single JSONL line larger than max_chars is split character-level."""
    max_tokens = 10_000
    # effective max_chars = (10000 - 5000) * 5 // 2 = 12500
    max_chars = 12_500
    oversized = '{"huge": "' + "x" * 20_000 + '"}'
    text = oversized
    windows = window_transcript_jsonl(text, max_tokens=max_tokens, overlap_tokens=500)
    assert len(windows) >= 2
    for w in windows:
        assert len(w) <= max_chars


def test_jsonl_oversized_line_among_normal_lines():
    """An oversized line in the middle flushes the current window and splits."""
    max_tokens = 10_000
    max_chars = 12_500  # (10000 - 5000) * 5 // 2
    normal_lines = [f'{{"i": {i}}}' for i in range(10)]
    oversized = '{"big": "' + "b" * 20_000 + '"}'
    more_lines = [f'{{"j": {j}}}' for j in range(10)]
    text = "\n".join(normal_lines + [oversized] + more_lines)
    windows = window_transcript_jsonl(text, max_tokens=max_tokens, overlap_tokens=500)
    # Should have: window with normal_lines, split chunks of oversized, window with more_lines
    assert len(windows) >= 3
    for w in windows:
        assert len(w) <= max_chars


def test_jsonl_oversized_line_with_overlap():
    """Oversized line chunks overlap correctly."""
    max_tokens = 10_000
    overlap_tokens = 1000
    overlap_chars = overlap_tokens * 5 // 2  # 2500
    oversized = "Z" * 30_000
    windows = window_transcript_jsonl(oversized, max_tokens=max_tokens, overlap_tokens=overlap_tokens)
    assert len(windows) >= 2
    # Check overlap: end of window[0] should match start of window[1]
    overlap_region = windows[0][-overlap_chars:]
    assert windows[1].startswith(overlap_region)


# ---------------------------------------------------------------------------
# Overlap carry between normal windows
# ---------------------------------------------------------------------------


def test_jsonl_overlap_carry():
    """Lines at the end of one window are carried to the start of the next."""
    max_tokens = 10_000
    overlap_tokens = 2000
    # max_chars = 12500, overlap_chars = 5000
    # Create lines of ~500 chars each -> ~25 lines per window
    lines = [f'{{"idx": {i}, "payload": "{"p" * 480}"}}' for i in range(60)]
    text = "\n".join(lines)
    windows = window_transcript_jsonl(text, max_tokens=max_tokens, overlap_tokens=overlap_tokens)
    assert len(windows) >= 2
    # Last lines of window 0 should appear at start of window 1
    w0_lines = windows[0].split("\n")
    w1_lines = windows[1].split("\n")
    # At least some overlap lines should match
    overlap_count = 0
    for line in w0_lines[-10:]:
        if line in w1_lines[:15]:
            overlap_count += 1
    assert overlap_count > 0, "Expected overlap lines between consecutive windows"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_jsonl_empty_text():
    """Empty text returns single-element list."""
    windows = window_transcript_jsonl("", max_tokens=10_000, overlap_tokens=100)
    assert windows == [""]


def test_jsonl_single_line_within_limit():
    """Single line within limit returns one window."""
    line = '{"key": "value"}'
    windows = window_transcript_jsonl(line, max_tokens=100_000, overlap_tokens=1000)
    assert windows == [line]


def test_jsonl_headroom_floor():
    """When max_tokens is very small, effective_tokens floors at max_tokens // 2."""
    # max_tokens = 6000, headroom = 5000 -> effective = max(1000, 3000) = 3000
    max_tokens = 6000
    effective_tokens = max(max_tokens - 5000, max_tokens // 2)
    assert effective_tokens == 3000
    max_chars = effective_tokens * 5 // 2  # 7500
    text = "\n".join(["x" * 100] * 100)  # ~10100 chars
    windows = window_transcript_jsonl(text, max_tokens=max_tokens, overlap_tokens=0)
    assert len(windows) >= 2
    for w in windows:
        assert len(w) <= max_chars
