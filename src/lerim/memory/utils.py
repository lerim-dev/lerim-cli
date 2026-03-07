"""Shared DSPy helpers used by memory extraction and trace summarization pipelines.

Provides LM configuration, token estimation, and transcript windowing.
"""

from __future__ import annotations

import dspy
from lerim.config.logging import logger
from lerim.config.settings import get_config
from lerim.runtime.providers import build_dspy_lm


def configure_dspy_lm(role: str = "extract") -> dspy.LM:
    """Build DSPy LM for a role without global configuration.

    Returns the LM object. Callers must use dspy.context(lm=lm) for
    thread-safe execution instead of dspy.configure().
    """
    config = get_config()
    normalized_role = "summarize" if role == "summarize" else "extract"
    role_cfg = (
        config.summarize_role if normalized_role == "summarize" else config.extract_role
    )
    logger.info(
        "Configuring DSPy LM for role={} provider={} model={}",
        normalized_role,
        role_cfg.provider,
        role_cfg.model,
    )
    return build_dspy_lm(normalized_role, config=config)


def estimate_tokens(text: str) -> int:
    """Estimate token count from text length (conservative for JSON content)."""
    return len(text) * 2 // 7


def window_transcript(
    text: str,
    max_tokens: int,
    overlap_tokens: int,
) -> list[str]:
    """Split text into overlapping windows based on character count derived from token estimates.

    Uses max_chars = max_tokens * 7 // 2 and overlap_chars = overlap_tokens * 7 // 2.
    If the text fits in one window, returns [text].
    """
    max_chars = max_tokens * 7 // 2
    overlap_chars = overlap_tokens * 7 // 2
    if len(text) <= max_chars:
        return [text]
    step = max_chars - overlap_chars
    if step <= 0:
        step = max_chars
    windows: list[str] = []
    pos = 0
    while pos < len(text):
        windows.append(text[pos : pos + max_chars])
        pos += step
    return windows


def window_transcript_jsonl(
    text: str,
    max_tokens: int,
    overlap_tokens: int,
) -> list[str]:
    """Split JSONL text into windows on line boundaries with token-estimated sizing.

    Uses a tighter token ratio (2.5 chars/token) than plain-text windowing
    because JSON has more short tokens ({, ", :, keys). Reserves headroom
    for DSPy prompt overhead (signature, metadata, output schema).
    """
    prompt_headroom_tokens = 5000
    effective_tokens = max(max_tokens - prompt_headroom_tokens, max_tokens // 2)
    # JSON-aware ratio: ~2.5 chars per token (tighter than 3.5 for prose)
    max_chars = effective_tokens * 5 // 2
    overlap_chars = overlap_tokens * 5 // 2
    if len(text) <= max_chars:
        return [text]

    lines = text.split("\n")
    windows: list[str] = []
    current_lines: list[str] = []
    current_chars = 0

    for line in lines:
        line_chars = len(line) + 1  # +1 for newline

        # Oversized single line: split it character-level
        if line_chars > max_chars:
            # Flush current window first
            if current_lines:
                windows.append("\n".join(current_lines))
                current_lines = []
                current_chars = 0
            # Split the oversized line into char-level chunks
            step = max_chars - overlap_chars
            if step <= 0:
                step = max_chars
            pos = 0
            while pos < len(line):
                windows.append(line[pos : pos + max_chars])
                pos += step
            continue

        if current_chars + line_chars > max_chars and current_lines:
            windows.append("\n".join(current_lines))
            # Carry overlap: keep last N lines that fit in overlap budget
            overlap_lines: list[str] = []
            overlap_size = 0
            for prev_line in reversed(current_lines):
                if overlap_size + len(prev_line) + 1 > overlap_chars:
                    break
                overlap_lines.insert(0, prev_line)
                overlap_size += len(prev_line) + 1
            current_lines = overlap_lines
            current_chars = overlap_size
        current_lines.append(line)
        current_chars += line_chars

    if current_lines:
        windows.append("\n".join(current_lines))
    return windows if windows else [text]


if __name__ == "__main__":
    """Run direct smoke check for config-based DSPy setup and windowing."""
    config = get_config()
    assert config.extract_role.provider
    assert config.extract_role.model
    assert config.summarize_role.provider
    assert config.summarize_role.model

    # Token estimation
    assert estimate_tokens("") == 0
    assert estimate_tokens("a" * 7) == 2
    assert estimate_tokens("a" * 35) == 10

    # Windowing: small text fits in one window
    small = "x" * 100
    assert window_transcript(small) == [small]

    # Windowing: large text splits correctly
    big = "y" * 1_000_000
    windows = window_transcript(big, max_tokens=1000, overlap_tokens=100)
    assert len(windows) > 1
    # All text is covered
    assert windows[0].startswith("y")
    assert windows[-1].endswith("y")

    print(
        f"""\
DSPy config: \
extract={config.extract_role.provider}/{config.extract_role.model}, \
summarize={config.summarize_role.provider}/{config.summarize_role.model}, \
windowing=OK"""
    )
