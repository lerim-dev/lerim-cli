"""Extraction pipeline for session transcripts using ChainOfThought + windowing.

session file (.jsonl/.json) -> read text -> window -> dspy.ChainOfThought -> merge -> memory candidates.
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
from concurrent.futures import ThreadPoolExecutor, as_completed

from lerim.config.logging import logger
from lerim.config.settings import get_config
from lerim.memory.schemas import MemoryCandidate
from lerim.memory.utils import configure_dspy_lm, window_transcript, window_transcript_jsonl
from lerim.runtime.cost_tracker import capture_dspy_cost
from lerim.sessions import catalog as session_db


def _extraction_reward(_args, pred) -> float:
    """Return 1.0 if primitives is a non-empty list of valid MemoryCandidate items."""
    primitives = getattr(pred, "primitives", None)
    if not isinstance(primitives, list) or not primitives:
        return 0.0
    for item in primitives:
        if isinstance(item, dict):
            try:
                MemoryCandidate.model_validate(item)
            except Exception:
                return 0.0
    return 1.0


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
    metadata: dict[str, Any] = dspy.InputField(desc="Session metadata")
    metrics: dict[str, Any] = dspy.InputField(desc="Deterministic metrics")
    guidance: str = dspy.InputField(
        desc="Optional lead-agent natural language guidance about focus areas, trace context, and dedupe hints"
    )
    primitives: list[MemoryCandidate] = dspy.OutputField(
        desc="Extracted memory candidate list"
    )


class MemoryMergeSignature(dspy.Signature):
    """Merge and deduplicate memory candidates extracted from multiple transcript windows.

    Remove near-duplicates (keep highest-confidence version).
    Drop weak or redundant items. Return the final clean list.
    """

    candidates: list[dict] = dspy.InputField(
        desc="All per-window candidates as list of dicts"
    )
    metadata: dict = dspy.InputField(desc="Session metadata")
    primitives: list[MemoryCandidate] = dspy.OutputField(desc="Deduplicated final list")


def _process_window(extractor, window, meta, met, guid, wi, total, lm):
    """Process one extraction window in its own thread."""
    with dspy.context(lm=lm):
        w_start = time.time()
        result = extractor(transcript=window, metadata=meta, metrics=met, guidance=guid)
        candidates = []
        primitives = getattr(result, "primitives", [])
        if isinstance(primitives, list):
            for item in primitives:
                if isinstance(item, MemoryCandidate):
                    candidates.append(item.model_dump(mode="json", exclude_none=True))
                elif isinstance(item, dict):
                    candidates.append(item)
        elapsed = time.time() - w_start
        logger.info("  Window {}/{}: done ({:.1f}s, {} candidates)", wi, total, elapsed, len(candidates))
        return candidates


def _extract_candidates(
    transcript: str,
    *,
    metadata: dict[str, Any] | None = None,
    metrics: dict[str, Any] | None = None,
    guidance: str = "",
) -> list[dict[str, Any]]:
    """Run ChainOfThought extraction with windowing and return normalized candidates."""
    if not transcript.strip():
        return []
    config = get_config()
    max_window_tokens = config.extract_role.max_window_tokens
    overlap_tokens = config.extract_role.window_overlap_tokens
    if "\n{" in transcript:
        windows = window_transcript_jsonl(transcript, max_window_tokens, overlap_tokens)
    else:
        windows = window_transcript(transcript, max_window_tokens, overlap_tokens)
    logger.info("Extraction: {} window(s), max_window_tokens={}", len(windows), max_window_tokens)
    lm = configure_dspy_lm("extract")
    meta = metadata or {}
    met = metrics or {}
    guid = guidance.strip()

    all_candidates: list[dict[str, Any]] = []
    extractor = dspy.Refine(
        dspy.ChainOfThought(MemoryExtractSignature),
        N=2,
        reward_fn=_extraction_reward,
        threshold=1.0,
    )
    history_start = len(lm.history)
    max_workers = min(config.extract_role.max_workers, len(windows))
    if max_workers <= 1:
        with dspy.context(lm=lm):
            for wi, window in enumerate(windows, 1):
                logger.info("  Window {}/{}: extracting...", wi, len(windows))
                all_candidates.extend(
                    _process_window(extractor, window, meta, met, guid, wi, len(windows), lm)
                )
    else:
        logger.info("Processing {} windows with {} workers", len(windows), max_workers)
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(_process_window, extractor, window, meta, met, guid, wi, len(windows), lm): wi
                for wi, window in enumerate(windows, 1)
            }
            for future in as_completed(futures):
                all_candidates.extend(future.result())

    if not all_candidates:
        capture_dspy_cost(lm, history_start)
        return []

    # Single window: no merge needed
    if len(windows) == 1:
        capture_dspy_cost(lm, history_start)
        return all_candidates

    # Multiple windows: merge and deduplicate
    logger.info("Merging {} candidates from {} windows...", len(all_candidates), len(windows))
    merger = dspy.Refine(
        dspy.ChainOfThought(MemoryMergeSignature),
        N=2,
        reward_fn=_extraction_reward,
        threshold=1.0,
    )
    with dspy.context(lm=lm):
        merge_result = merger(candidates=all_candidates, metadata=meta)
    capture_dspy_cost(lm, history_start)
    merged = getattr(merge_result, "primitives", [])
    logger.info("Merge done: {} merged candidates", len(merged) if isinstance(merged, list) else 0)
    if not isinstance(merged, list):
        return all_candidates
    return [
        item.model_dump(mode="json", exclude_none=True)
        if isinstance(item, MemoryCandidate)
        else item
        for item in merged
        if isinstance(item, (MemoryCandidate, dict))
    ]


def extract_memories_from_session_file(
    session_file_path: Path,
    *,
    metadata: dict[str, Any] | None = None,
    metrics: dict[str, Any] | None = None,
    guidance: str = "",
) -> list[dict[str, Any]]:
    """Extract memory candidates from one on-disk session trace file."""
    if not session_file_path.exists() or not session_file_path.is_file():
        raise FileNotFoundError(f"session_file_missing:{session_file_path}")
    transcript = session_file_path.read_text(encoding="utf-8")
    return _extract_candidates(
        transcript,
        metadata=metadata,
        metrics=metrics,
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
    parser.add_argument("--metadata-json", default="{}")
    parser.add_argument("--metrics-json", default="{}")
    parser.add_argument("--guidance", default="")
    args = parser.parse_args()

    if args.trace_path:
        metadata = json.loads(args.metadata_json)
        metrics = json.loads(args.metrics_json)
        payload = extract_memories_from_session_file(
            Path(args.trace_path).expanduser(),
            metadata=metadata if isinstance(metadata, dict) else {},
            metrics=metrics if isinstance(metrics, dict) else {},
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
                metadata={"run_id": "self-test"},
                metrics={},
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
