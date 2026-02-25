"""Trace summarization pipeline that outputs markdown-frontmatter-ready metadata + summary.

When --memory-root is provided, the pipeline writes the summary markdown file
directly to memory_root/summaries/YYYYMMDD/HHMMSS/{slug}.md using python-frontmatter.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import dspy
import frontmatter
from pydantic import BaseModel, Field

from lerim.memory.memory_record import slugify
from lerim.config.settings import get_config, reload_config
from lerim.memory.utils import configure_dspy_lm, configure_dspy_sub_lm
from lerim.sessions import catalog as session_db


class TraceSummaryCandidate(BaseModel):
    """Structured trace summary payload used to build markdown frontmatter later."""

    title: str = Field(description="Short trace title for markdown frontmatter.")
    description: str = Field(
        description="One-line description of what the session achieved."
    )
    user_intent: str = Field(
        description="""\
The user's overall intention and request for this chat session. \
Not the literal query, but the broader goal the user was trying to \
accomplish. At most 150 words.""",
    )
    session_narrative: str = Field(
        description="""\
What actually happened over the course of the chat: actions taken, \
problems encountered, solutions applied, and final outcome. \
At most 200 words.""",
    )
    date: str = Field(description="Session date in YYYY-MM-DD.")
    time: str = Field(description="Session time in HH:MM:SS.")
    coding_agent: str = Field(
        description="Coding agent label like codex, claude code, cursor, or windsurf."
    )
    raw_trace_path: str = Field(
        description="Absolute path to the original raw trace file."
    )
    run_id: str | None = Field(
        default=None, description="Session run id when available."
    )
    repo_name: str | None = Field(
        default=None, description="Repository short name when available."
    )
    tags: list[str] = Field(
        default_factory=list,
        description="Group/cluster labels for this summary. No limit.",
    )


class TraceSummarySignature(dspy.Signature):
    """Summarize a raw coding-agent session trace into structured metadata plus a two-part summary.

    IMPORTANT -- Summarization strategy (you are running inside an RLM REPL):
    1) Do NOT normalize or pre-parse the transcript schema. Treat it as raw text.
       Different agents (Claude Code, Codex, Cursor, Windsurf) use different JSON shapes.
    2) First EXPLORE: sample spans from beginning, middle, and end to understand
       the structure, length, and agent format. Print samples, check types.
    3) Read the transcript in overlapping windows (~25,000 chars, ~2,000 overlap).
    4) For each window, use llm_query() to extract a running buffer of:
       - user goals and intentions
       - key actions taken
       - key failures, frictions, and blockers
       - fixes applied and outcomes achieved
    5) After all windows, MERGE the per-window buffers into a coherent narrative.
    6) Produce the final summary:
       - user_intent: the user's overall goal across the whole chat (at most 150 words).
         Not the literal first query, but the broader purpose.
       - session_narrative: what actually happened chronologically -- actions taken,
         problems hit, solutions applied, and final outcome (at most 200 words).
    7) Ground all claims in transcript evidence. Avoid invented details.
    8) Keep title and description concise and specific.
    9) Assign descriptive tags (group/cluster labels) for categorization. No limit on tag count.
    10) Extract date, time, coding_agent from transcript or metadata when available.
    """

    transcript: str = dspy.InputField(
        desc="Raw session transcript text (JSONL or JSON, schema varies by agent)"
    )
    metadata: dict[str, Any] = dspy.InputField(desc="Session metadata")
    metrics: dict[str, Any] = dspy.InputField(desc="Deterministic metrics")
    guidance: str = dspy.InputField(
        desc="Optional lead-agent natural language guidance about focus areas and trace context"
    )
    summary_payload: TraceSummaryCandidate = dspy.OutputField(
        desc="Structured summary payload with title/description/user_intent/session_narrative/date/time/agent/path/tags fields"
    )


def _summarize_trace_with_rlm(
    transcript: str,
    *,
    metadata: dict[str, Any] | None = None,
    metrics: dict[str, Any] | None = None,
    guidance: str = "",
) -> dict[str, Any]:
    """Run DSPy RLM summarization and return schema-validated summary metadata payload."""
    if not transcript.strip():
        raise RuntimeError("session_trace_empty")
    config = get_config()
    max_iterations = config.summarize_role.max_iterations
    max_llm_calls = config.summarize_role.max_llm_calls
    lm = configure_dspy_lm("summarize")
    sub_lm = configure_dspy_sub_lm("summarize")
    rlm = dspy.RLM(
        TraceSummarySignature,
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
                guidance=guidance.strip(),
            )
        except Exception:
            predictor = dspy.Predict(TraceSummarySignature)
            result = predictor(
                transcript=transcript,
                metadata=metadata or {},
                metrics=metrics or {},
                guidance=guidance.strip(),
            )
    payload = getattr(result, "summary_payload", None)
    if isinstance(payload, TraceSummaryCandidate):
        candidate = payload
    elif isinstance(payload, dict):
        candidate = TraceSummaryCandidate.model_validate(payload)
    else:
        raise RuntimeError("dspy summary_payload must be TraceSummaryCandidate or dict")
    return candidate.model_dump(mode="json", exclude_none=True)


def write_summary_markdown(
    payload: dict[str, Any],
    memory_root: Path,
    *,
    run_id: str = "",
) -> Path:
    """Write summary markdown with frontmatter to memory_root/summaries/YYYYMMDD/HHMMSS/{slug}.md."""
    title = str(payload.get("title") or "untitled")
    user_intent = str(payload.get("user_intent") or "")
    session_narrative = str(payload.get("session_narrative") or "")
    summary_body = (
        f"## User Intent\n\n{user_intent}\n\n## What Happened\n\n{session_narrative}"
    )
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    fm_dict: dict[str, Any] = {
        "id": slugify(title),
        "title": title,
        "created": now_iso,
        "source": run_id,
        "description": payload.get("description", ""),
        "date": payload.get("date", now_iso[:10]),
        "time": payload.get("time", now_iso[11:19]),
        "coding_agent": payload.get("coding_agent", "unknown"),
        "raw_trace_path": payload.get("raw_trace_path", ""),
        "run_id": payload.get("run_id") or run_id,
        "repo_name": payload.get("repo_name", ""),
        "tags": payload.get("tags", []),
    }

    slug = slugify(title)
    date_compact = re.sub(r"[^0-9]", "", str(payload.get("date", now_iso[:10])))[:8]
    time_compact = re.sub(r"[^0-9]", "", str(payload.get("time", now_iso[11:19])))[:6]
    if len(date_compact) != 8 or len(time_compact) != 6:
        date_compact = datetime.now(timezone.utc).strftime("%Y%m%d")
        time_compact = datetime.now(timezone.utc).strftime("%H%M%S")
    summaries_dir = memory_root / "summaries" / date_compact / time_compact
    summaries_dir.mkdir(parents=True, exist_ok=True)
    summary_path = summaries_dir / f"{slug}.md"

    post = frontmatter.Post(summary_body, **fm_dict)
    summary_path.write_text(frontmatter.dumps(post) + "\n", encoding="utf-8")
    return summary_path


def summarize_trace_from_session_file(
    session_file_path: Path,
    *,
    metadata: dict[str, Any] | None = None,
    metrics: dict[str, Any] | None = None,
    guidance: str = "",
) -> dict[str, Any]:
    """Summarize one session trace file into markdown-ready metadata + <=300 word summary."""
    if not session_file_path.exists() or not session_file_path.is_file():
        raise FileNotFoundError(f"session_file_missing:{session_file_path}")
    transcript = session_file_path.read_text(encoding="utf-8")
    session_metadata = {**(metadata or {}), "raw_trace_path": str(session_file_path)}
    return _summarize_trace_with_rlm(
        transcript,
        metadata=session_metadata,
        metrics=metrics,
        guidance=guidance,
    )


if __name__ == "__main__":
    """Run CLI summary mode by trace path or run a real-path self-test."""
    parser = argparse.ArgumentParser(
        prog="python -m lerim.memory.summarization_pipeline"
    )
    parser.add_argument("--trace-path")
    parser.add_argument("--output")
    parser.add_argument(
        "--memory-root",
        help="When provided, write summary markdown to memory_root/summaries/",
    )
    parser.add_argument("--metadata-json", default="{}")
    parser.add_argument("--metrics-json", default="{}")
    parser.add_argument("--guidance", default="")
    args = parser.parse_args()

    if args.trace_path:
        session_file = Path(args.trace_path).expanduser()
        metadata = json.loads(args.metadata_json)
        metrics = json.loads(args.metrics_json)
        payload = summarize_trace_from_session_file(
            session_file,
            metadata=metadata if isinstance(metadata, dict) else {},
            metrics=metrics if isinstance(metrics, dict) else {},
            guidance=args.guidance,
        )

        # Write summary markdown and output pointer
        if not args.memory_root:
            raise SystemExit("--memory-root is required")
        mr = Path(args.memory_root).expanduser().resolve()
        run_id = (metadata if isinstance(metadata, dict) else {}).get("run_id", "")
        summary_path = write_summary_markdown(payload, mr, run_id=run_id)
        output_data = {"summary_path": str(summary_path)}

        encoded = json.dumps(output_data, ensure_ascii=True, indent=2) + "\n"
        if args.output:
            output_path = Path(args.output).expanduser()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(encoded, encoding="utf-8")
        else:
            sys.stdout.write(encoded)
    else:
        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            cfg_path = tmp_path / "test_config.toml"
            cfg_path.write_text(
                f'[data]\ndir = "{tmp_path}"\n\n[memory]\nscope = "global_only"\n',
                encoding="utf-8",
            )
            prev_cfg = os.environ.get("LERIM_CONFIG")
            os.environ["LERIM_CONFIG"] = str(cfg_path)
            reload_config()
            session_db.init_sessions_db()

            run_id = "summary-self-test-1"
            session_path = tmp_path / "sessions" / f"{run_id}.jsonl"
            session_path.parent.mkdir(parents=True, exist_ok=True)
            session_path.write_text(
                '{"role":"user","content":"Fix queue heartbeat drift and duplicate claims."}\n'
                '{"role":"assistant","content":"Implemented heartbeat every 15 seconds and bounded retries."}\n',
                encoding="utf-8",
            )
            session_db.index_session_for_fts(
                run_id=run_id,
                agent_type="codex",
                repo_name="lerim",
                content="queue heartbeat fix",
                session_path=str(session_path),
            )
            payload = summarize_trace_from_session_file(
                session_path, metadata={"run_id": run_id}, metrics={}
            )

        if prev_cfg is None:
            os.environ.pop("LERIM_CONFIG", None)
        else:
            os.environ["LERIM_CONFIG"] = prev_cfg
        reload_config()
        assert isinstance(payload, dict)
        assert payload["title"]
        assert payload["description"]
        assert payload["coding_agent"] == "codex"
        assert payload["raw_trace_path"].endswith(f"{run_id}.jsonl")
        assert payload["user_intent"]
        assert len(payload["user_intent"].split()) <= 150
        assert payload["session_narrative"]
        assert len(payload["session_narrative"].split()) <= 200
