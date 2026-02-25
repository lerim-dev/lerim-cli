"""Minimal extraction pipeline for session transcripts.

The extraction path is intentionally simple:
session file (.jsonl/.json) -> read text -> dspy.RLM -> memory candidates.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import dspy

from lerim.config.settings import get_config
from lerim.memory.schemas import MemoryCandidate
from lerim.memory.utils import configure_dspy_lm, configure_dspy_sub_lm
from lerim.sessions import catalog as session_db


class MemoryExtractSignature(dspy.Signature):
    """Extract reusable memory candidates from a raw coding-agent session transcript.

    IMPORTANT -- Extraction strategy (you are running inside an RLM REPL):
    1) Do NOT normalize or pre-parse the transcript schema. Treat it as raw text.
       Different agents (Claude Code, Codex, Cursor, Windsurf) use different JSON shapes.
    2) First EXPLORE: sample a few spans from beginning, middle, and end to understand
       the structure and length. Print samples, check types. Do not try to solve in one step.
    3) Build overlapping windows of ~20,000 characters with ~2,000 overlap.
    4) For each window, use llm_query() to extract only durable, high-value items:
       - decision: explicit stable choice / configuration / policy.
         Trigger words: "decision", "we will", "use X not Y", "always do", "never do", "set X to Y".
       - learning: reusable lesson / fix / pitfall / friction signal / user preference / habit.
         Trigger words: "lesson", "fix", "found that", "struggled with", "wasted time on",
         "I prefer", "I like", "I want", "I always", "my style", "don't like", "hate when",
         "keep it", "make sure", "never use", "always use", "I usually", "my convention".
       - When in doubt, prefer learning.
    5) For every extracted item, keep one short verbatim evidence quote (<=200 chars).
    6) After processing all windows, MERGE near-duplicates across windows.
    7) Prefer precision over recall. If evidence is weak, drop it.
    8) SUBMIT only the final deduplicated primitives list.

    Kind (for learnings only):
    - insight: a reusable observation or pattern.
    - procedure: a step-by-step fix or workflow.
    - friction: a blocker, struggle, or time-waster.
    - pitfall: a mistake to avoid.
    - preference: a user preference, habit, convention, or style choice.
      Examples: coding style, tool choices, naming conventions, communication preferences,
      workflow habits, formatting rules, library preferences.

    Tags: assign descriptive group/cluster labels for categorization. No limit on count.
    Examples: queue, heartbeat, docker, ci-cd, patching, error-handling, coding-style, naming.

    Focus on high-value items:
    - repeated struggles and blockers
    - lessons and fixes that worked
    - decisions to reuse later
    - user preferences, habits, and conventions (what the user likes/dislikes, how they work)
    """

    transcript: str = dspy.InputField(
        desc="Raw session transcript text (JSONL or JSON, schema varies by agent)"
    )
    metadata: dict[str, Any] = dspy.InputField(desc="Session metadata")
    metrics: dict[str, Any] = dspy.InputField(desc="Deterministic metrics")
    primitives: list[MemoryCandidate] = dspy.OutputField(
        desc="Extracted memory candidate list"
    )


def _extract_candidates_with_rlm(
    transcript: str,
    *,
    metadata: dict[str, Any] | None = None,
    metrics: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Run DSPy RLM on transcript text and return normalized candidates."""
    if not transcript.strip():
        return []
    config = get_config()
    max_iterations = config.dspy_rlm_max_iterations
    max_llm_calls = config.dspy_rlm_max_llm_calls
    lm = configure_dspy_lm("extract")
    sub_lm = configure_dspy_sub_lm("extract")
    rlm = dspy.RLM(
        MemoryExtractSignature,
        max_iterations=max_iterations,
        max_llm_calls=max_llm_calls,
        sub_lm=sub_lm,
        verbose=False,
    )
    with dspy.context(lm=lm):
        try:
            result = rlm(
                transcript=transcript,
                metadata=metadata or {},
                metrics=metrics or {},
            )
        except Exception:
            result = None
        # Fallback to Predict if RLM failed or returned empty candidates
        if result is None or not getattr(result, "primitives", None):
            predictor = dspy.Predict(MemoryExtractSignature)
            result = predictor(
                transcript=transcript,
                metadata=metadata or {},
                metrics=metrics or {},
            )
    primitives = getattr(result, "primitives", [])
    if not isinstance(primitives, list):
        return []
    return [
        item.model_dump(mode="json", exclude_none=True)
        if isinstance(item, MemoryCandidate)
        else item
        for item in primitives
        if isinstance(item, (MemoryCandidate, dict))
    ]


def extract_memories_from_session_file(
    session_file_path: Path,
    *,
    metadata: dict[str, Any] | None = None,
    metrics: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Extract memory candidates from one on-disk session trace file."""
    if not session_file_path.exists() or not session_file_path.is_file():
        raise FileNotFoundError(f"session_file_missing:{session_file_path}")
    transcript = session_file_path.read_text(encoding="utf-8")
    return _extract_candidates_with_rlm(transcript, metadata=metadata, metrics=metrics)


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
    args = parser.parse_args()

    if args.trace_path:
        metadata = json.loads(args.metadata_json)
        metrics = json.loads(args.metrics_json)
        payload = extract_memories_from_session_file(
            Path(args.trace_path).expanduser(),
            metadata=metadata if isinstance(metadata, dict) else {},
            metrics=metrics if isinstance(metrics, dict) else {},
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
