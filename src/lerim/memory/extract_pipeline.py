"""Extraction pipeline for session transcripts using Predict + windowing.

session file (.jsonl/.json) -> read text -> window (if needed) -> dspy.Predict
-> concat candidates from all windows.

Traces are compacted by adapters (tool outputs stripped), so most traces fit in a
single window. Windowing is a fallback for unusually large sessions. No merge or
deduplication — the downstream maintain path handles that.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import dspy

from lerim.config.logging import logger
from lerim.config.settings import get_config
from lerim.memory.schemas import MemoryCandidate
from lerim.memory.utils import (
    call_with_fallback,
    configure_dspy_lms,
    window_transcript,
    window_transcript_jsonl,
)
from lerim.runtime.cost_tracker import capture_dspy_cost
from lerim.sessions import catalog as session_db


class MemoryExtractSignature(dspy.Signature):
    """Extract reusable memory candidates from this transcript segment.

    Focus on decisions (explicit choices/policies) and learnings
    (lessons/fixes/pitfalls/preferences). Keep one short evidence quote
    per item (<=200 chars). Prefer precision over recall.

    Kind (for learnings only):
    - insight: a reusable observation or pattern.
    - procedure: a step-by-step fix or workflow.
    - friction: a blocker, struggle, or time-waster.
    - pitfall: a mistake to avoid.
    - preference: a user preference, habit, convention, or style choice.

    Tags: assign descriptive group/cluster labels for categorization.
    """

    transcript: str = dspy.InputField(
        desc="Raw session transcript text (JSONL or JSON, schema varies by agent)"
    )
    guidance: str = dspy.InputField(
        desc="Optional lead-agent natural language guidance about focus areas, trace context, and dedupe hints"
    )
    primitives: list[MemoryCandidate] = dspy.OutputField(
        desc="Extracted memory candidate list"
    )


def _extract_candidates(
    transcript: str,
    *,
    guidance: str = "",
) -> list[dict[str, Any]]:
    """Run Predict extraction with windowing and return normalized candidates.

    Processes each window independently and concatenates all candidates.
    No merge or deduplication — maintain handles that downstream.
    """
    if not transcript.strip():
        return []
    config = get_config()
    max_window_tokens = config.extract_role.max_window_tokens
    overlap_tokens = config.extract_role.window_overlap_tokens
    if "\n{" in transcript:
        windows = window_transcript_jsonl(transcript, max_window_tokens, overlap_tokens)
    else:
        windows = window_transcript(transcript, max_window_tokens, overlap_tokens)
    logger.info(
        "Extraction: {} window(s), max_window_tokens={}",
        len(windows),
        max_window_tokens,
    )
    lms = configure_dspy_lms("extract")
    guid = guidance.strip()

    all_candidates: list[dict[str, Any]] = []
    extractor = dspy.Predict(MemoryExtractSignature)
    history_start = len(lms[0].history)
    for wi, window in enumerate(windows, 1):
        logger.info("  Window {}/{}: extracting...", wi, len(windows))
        w_start = time.time()
        used_lm, result = call_with_fallback(
            extractor, lms, transcript=window, guidance=guid
        )
        primitives = getattr(result, "primitives", [])
        if isinstance(primitives, list):
            for item in primitives:
                if isinstance(item, MemoryCandidate):
                    all_candidates.append(
                        item.model_dump(mode="json", exclude_none=True)
                    )
                elif isinstance(item, dict):
                    all_candidates.append(item)
        logger.info(
            "  Window {}/{}: done ({:.1f}s, {} candidates)",
            wi,
            len(windows),
            time.time() - w_start,
            len(primitives) if isinstance(primitives, list) else 0,
        )
    capture_dspy_cost(lms[0], history_start)
    return all_candidates


def extract_memories_from_session_file(
    session_file_path: Path,
    *,
    guidance: str = "",
) -> list[dict[str, Any]]:
    """Extract memory candidates from one on-disk session trace file."""
    if not session_file_path.exists() or not session_file_path.is_file():
        raise FileNotFoundError(f"session_file_missing:{session_file_path}")
    transcript = session_file_path.read_text(encoding="utf-8")
    return _extract_candidates(
        transcript,
        guidance=guidance,
    )


def build_extract_report(
    *,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
    agent_types: list[str] | None = None,
) -> dict[str, Any]:
    """Build aggregate extraction stats for dashboard and maintenance views."""
    rows, _ = session_db.list_sessions_window(
        limit=500,
        offset=0,
        agent_types=agent_types,
        since=window_start,
        until=window_end,
    )
    totals = defaultdict(int)
    for row in rows:
        totals["sessions"] += 1
        totals["messages"] += int(row.get("message_count") or 0)
        totals["tool_calls"] += int(row.get("tool_call_count") or 0)
        totals["errors"] += int(row.get("error_count") or 0)
        totals["tokens"] += int(row.get("total_tokens") or 0)
    return {
        "window_start": window_start.isoformat() if window_start else None,
        "window_end": window_end.isoformat() if window_end else None,
        "agent_filter": ",".join(agent_types) if agent_types else "all",
        "aggregates": {"totals": dict(totals)},
        "narratives": {
            "at_a_glance": {
                "working": "",
                "hindering": "",
                "quick_wins": "",
                "horizon": "",
            }
        },
    }


if __name__ == "__main__":
    """Run CLI extract mode by trace path or run a real-path self-test."""
    parser = argparse.ArgumentParser(prog="python -m lerim.memory.extract_pipeline")
    parser.add_argument("--trace-path")
    parser.add_argument("--output")
    parser.add_argument("--guidance", default="")
    args = parser.parse_args()

    if args.trace_path:
        payload = extract_memories_from_session_file(
            Path(args.trace_path).expanduser(),
            guidance=args.guidance,
        )
        encoded = json.dumps(payload, ensure_ascii=True, indent=2) + "\n"
        if args.output:
            output_path = Path(args.output).expanduser()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(encoded, encoding="utf-8")
        else:
            sys.stdout.write(encoded)
    else:
        with TemporaryDirectory() as tmp_dir:
            session_file_path = Path(tmp_dir) / "session.jsonl"
            session_file_path.write_text(
                "\n".join(
                    [
                        '{"role":"user","content":"I keep failing the same edit because the target string exists in test and src. This friction wasted 30 minutes."}',
                        '{"role":"assistant","content":"Lesson: read the exact file first, then patch with file path and larger context. Avoid global replace."}',
                        '{"role":"user","content":"Queue jobs got stuck again. Heartbeat drift caused retries and duplicate claims."}',
                        '{"role":"assistant","content":"Fix worked: heartbeat every 15s, max_attempts=3, then dead_letter. Add metrics for retries and dead letters."}',
                        '{"role":"user","content":"Decision: do not copy traces into Lerim. Keep only session_path and metadata; extract directly from source file."}',
                        '{"role":"user","content":"I prefer short functions, max 20 lines. I always want docstrings on every function. Never use abbreviations in variable names."}',
                        '{"role":"assistant","content":"Got it. I will keep functions under 20 lines, add docstrings everywhere, and use full descriptive variable names."}',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            candidates = extract_memories_from_session_file(
                session_file_path,
            )
        assert candidates, "self-test failed: no candidates extracted"

        quality_hits = 0
        for item in candidates:
            assert isinstance(item, dict), "self-test failed: candidate must be dict"
            primitive = str(item.get("primitive") or "").strip()
            title = str(item.get("title") or "").strip()
            body = str(item.get("body") or "").strip()

            assert primitive in {"decision", "learning"}, (
                f"self-test failed: invalid primitive={primitive!r}"
            )
            assert len(title) >= 8, "self-test failed: title too short"
            assert len(body) >= 24, "self-test failed: body too short"

            text_blob = f"{title} {body}".lower()
            kind_val = str(item.get("kind") or "").strip().lower()
            if any(
                keyword in text_blob
                for keyword in (
                    "heartbeat",
                    "dead_letter",
                    "file path",
                    "retry",
                    "friction",
                    "short function",
                    "docstring",
                    "abbreviat",
                    "variable name",
                    "20 line",
                )
            ):
                quality_hits += 1
            if kind_val == "preference":
                quality_hits += 1

        assert quality_hits >= 2, (
            "self-test failed: extracted memories miss expected session signals "
            "(need at least technical + preference hits)"
        )
