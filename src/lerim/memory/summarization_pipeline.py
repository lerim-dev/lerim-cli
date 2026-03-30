"""Trace summarization pipeline using DSPy modules + parallel MapReduce.

Outputs markdown-frontmatter-ready metadata + summary.
When --memory-root is provided, writes the summary markdown file
directly to memory_root/summaries/YYYYMMDD/HHMMSS/{slug}.md using python-frontmatter.

Traces are compacted by adapters (tool outputs stripped), so most traces fit in a
single LLM call. For oversized traces, a parallel MapReduce with tree reduction
summarizes chunks concurrently, then merges them hierarchically.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import dspy
import frontmatter
from pydantic import BaseModel, Field

from lerim.config.logging import logger
from lerim.memory.memory_record import slugify
from lerim.config.settings import get_config, reload_config
from lerim.memory.utils import (
    call_with_fallback,
    configure_dspy_lms,
    estimate_tokens,
    window_transcript,
    window_transcript_jsonl,
)
from lerim.memory.extract_pipeline import _format_transcript_for_extraction
from lerim.runtime.cost_tracker import capture_dspy_cost
from lerim.sessions import catalog as session_db


class TraceSummaryCandidate(BaseModel):
    """Structured trace summary payload from LLM. Deterministic fields (date, time, coding_agent, etc.) are merged in Python."""

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
    tags: list[str] = Field(
        default_factory=list,
        description="Group/cluster labels for this summary. No limit.",
    )


class TraceSummarySignature(dspy.Signature):
    """Summarize a raw coding-agent session trace into a structured summary.

    Produce a concise summary with:
    - user_intent: the user's overall goal (at most 150 words)
    - session_narrative: what happened chronologically (at most 200 words)
    - title and description: concise and specific
    - tags: descriptive group/cluster labels

    Ground all claims in transcript evidence. Avoid invented details.
    """

    transcript: str = dspy.InputField(
        desc="Raw session transcript text (JSONL or JSON, schema varies by agent)"
    )
    guidance: str = dspy.InputField(
        desc="Optional lead-agent natural language guidance about focus areas and trace context"
    )
    summary_payload: TraceSummaryCandidate = dspy.OutputField(
        desc="Structured summary payload with title/description/user_intent/session_narrative/tags fields"
    )


class ChunkFacetSignature(dspy.Signature):
	"""Extract a lightweight facet from one chunk of a session transcript.

	Produce a BRIEF bullet-point summary of what happened in this chunk.
	3-5 bullets max. Focus on:
	- Decisions made (by user or agent)
	- Problems encountered and how they were resolved
	- Key tools or files involved
	- Outcomes and results

	Be extremely concise — this will be merged with other chunk facets to
	produce a full session summary. Do NOT write full paragraphs.
	"""

	transcript: str = dspy.InputField(
		desc="One chunk of a session transcript",
	)
	chunk_position: str = dspy.InputField(
		desc="e.g. 'chunk 3 of 73' — helps orient within the session",
	)
	facet: str = dspy.OutputField(
		desc="3-5 bullet points capturing key events, decisions, and outcomes from this chunk. Max 80 words.",
	)
	tags: list[str] = dspy.OutputField(
		desc="2-5 topic tags for this chunk",
	)


class SynthesizeSummarySignature(dspy.Signature):
	"""Synthesize a full session summary from ordered chunk facets.

	You receive lightweight facets (bullet points) from consecutive chunks
	of the SAME coding session, in chronological order. Produce a coherent
	structured summary of the ENTIRE session.

	The facets are brief — your job is to weave them into a narrative that
	reads as one coherent story, not a list of per-chunk bullets.
	"""

	ordered_facets: str = dspy.InputField(
		desc="Chronologically ordered chunk facets, formatted as 'Chunk N: [bullets]\\n...'",
	)
	summary_payload: TraceSummaryCandidate = dspy.OutputField(
		desc="Full session summary with title, description, user_intent, session_narrative, and tags",
	)


class MergeFacetsSignature(dspy.Signature):
	"""Merge multiple chunk facets into a condensed intermediate facet.

	Used in tree reduction when too many facets to fit in one synthesis call.
	Combine the input facets into a single, slightly longer facet that
	preserves the key events and decisions from all inputs.
	"""

	ordered_facets: str = dspy.InputField(
		desc="Chronologically ordered chunk facets to merge",
	)
	chunk_range: str = dspy.InputField(
		desc="Which portion these facets cover, e.g. 'chunks 1-8 of 73'",
	)
	merged_facet: str = dspy.OutputField(
		desc="Condensed facet covering all input chunks. 5-10 bullet points, max 150 words.",
	)
	tags: list[str] = dspy.OutputField(
		desc="Combined topic tags from all merged chunks",
	)


class TraceSummarizationPipeline(dspy.Module):
	"""Two-phase summarization: parallel map (tiny facets) → reduce (synthesize).

	Map: Extract lightweight facets from each chunk (3-5 bullets, ~80 words each).
	Reduce: If all facets fit in context, single synthesis call. Otherwise tree merge
	        facets down, then synthesize.

	All signatures are optimizable by DSPy (MIPROv2, BootstrapFewShot, etc.).
	"""

	def __init__(self):
		super().__init__()
		self.extract_facet = dspy.Predict(ChunkFacetSignature)
		self.merge_facets = dspy.Predict(MergeFacetsSignature)
		self.synthesize = dspy.Predict(SynthesizeSummarySignature)

	def forward(self, windows: list[str], guidance: str = "") -> dspy.Prediction:
		# Map: extract tiny facet per chunk
		facets: list[dict[str, Any]] = []
		for i, window in enumerate(windows, 1):
			result = self.extract_facet(
				transcript=window,
				chunk_position=f"chunk {i} of {len(windows)}",
			)
			facets.append({
				"chunk": i,
				"facet": str(result.facet),
				"tags": list(result.tags) if isinstance(result.tags, list) else [],
			})

		if not facets:
			raise RuntimeError("map_phase_produced_no_facets")

		# Reduce: tree merge if needed, then synthesize
		formatted = _format_facets(facets)
		result = self.synthesize(ordered_facets=formatted)
		return dspy.Prediction(summary_payload=result.summary_payload)


def _format_facets(facets: list[dict[str, Any]]) -> str:
	"""Format facets as readable text for synthesis/merge input."""
	parts: list[str] = []
	for f in facets:
		parts.append(f"Chunk {f['chunk']}:\n{f['facet']}")
	return "\n\n".join(parts)


def _extract_one_facet(
	wi: int,
	total: int,
	window: str,
) -> dict[str, Any] | None:
	"""Extract a lightweight facet from one chunk (thread-safe).

	Returns None if extraction fails (chunk is skipped in reduce phase).
	"""
	lms = configure_dspy_lms("summarize")
	extractor = dspy.Predict(ChunkFacetSignature)
	history_start = len(lms[0].history)
	w_start = time.time()
	try:
		_, result = call_with_fallback(
			extractor, lms,
			transcript=window,
			chunk_position=f"chunk {wi} of {total}",
		)
		logger.info("  Map {}/{}: done ({:.1f}s)", wi, total, time.time() - w_start)
		capture_dspy_cost(lms[0], history_start)
		return {
			"chunk": wi,
			"facet": str(result.facet),
			"tags": list(result.tags) if isinstance(result.tags, list) else [],
		}
	except Exception:
		logger.warning("  Map {}/{}: failed ({:.1f}s), skipping", wi, total, time.time() - w_start)
		capture_dspy_cost(lms[0], history_start)
		return None


def _merge_facet_batch(
	facets: list[dict[str, Any]],
	batch_idx: int,
	level: int,
) -> dict[str, Any] | None:
	"""Merge a batch of facets into one condensed facet (thread-safe)."""
	lms = configure_dspy_lms("summarize")
	merger = dspy.Predict(MergeFacetsSignature)
	history_start = len(lms[0].history)
	w_start = time.time()
	chunk_range = f"chunks {facets[0]['chunk']}-{facets[-1]['chunk']}"
	try:
		_, result = call_with_fallback(
			merger, lms,
			ordered_facets=_format_facets(facets),
			chunk_range=chunk_range,
		)
		all_tags: list[str] = []
		for f in facets:
			all_tags.extend(f.get("tags", []))
		merged_tags = list(result.tags) if isinstance(result.tags, list) else all_tags
		logger.info("  Reduce L{} group {}: done ({:.1f}s)", level, batch_idx + 1, time.time() - w_start)
		capture_dspy_cost(lms[0], history_start)
		return {
			"chunk": facets[0]["chunk"],
			"facet": str(result.merged_facet),
			"tags": sorted(set(merged_tags)),
		}
	except Exception:
		logger.warning("  Reduce L{} group {}: failed ({:.1f}s)", level, batch_idx + 1, time.time() - w_start)
		capture_dspy_cost(lms[0], history_start)
		return None


def _map_and_reduce(
	windows: list[str],
	guidance: str,
	max_workers: int,
	facet_context_budget: int = 80000,
	batch_size: int = 10,
) -> TraceSummaryCandidate:
	"""Parallel map (tiny facets) → optional tree reduce → final synthesis.

	Map phase: Extract lightweight facets from each chunk in parallel (~80 words each).
	Reduce phase: If all facets fit in context budget, single synthesis call.
	              Otherwise, tree-merge facets down until they fit, then synthesize.
	"""
	total = len(windows)

	# ── Map phase: parallel facet extraction ──
	logger.info("Summarization: {} windows (map-reduce), {} workers", total, max_workers)
	facet_slots: list[dict[str, Any] | None] = [None] * total

	if max_workers > 1 and total > 1:
		effective_workers = min(max_workers, total)
		with ThreadPoolExecutor(max_workers=effective_workers) as pool:
			futures = {
				pool.submit(_extract_one_facet, wi, total, window): wi
				for wi, window in enumerate(windows, 1)
			}
			for future in as_completed(futures):
				wi = futures[future]
				facet_slots[wi - 1] = future.result()
	else:
		for wi, window in enumerate(windows, 1):
			facet_slots[wi - 1] = _extract_one_facet(wi, total, window)

	# Filter out failed chunks, preserving order
	facets = [f for f in facet_slots if f is not None]
	if not facets:
		raise RuntimeError("map_phase_produced_no_facets")
	logger.info("Map phase: {}/{} facets extracted", len(facets), total)

	# ── Reduce phase: tree merge if facets exceed context budget ──
	formatted = _format_facets(facets)
	est_tokens = estimate_tokens(formatted)
	level = 0

	while est_tokens > facet_context_budget and len(facets) > 1:
		logger.info("Reduce level {}: {} facets (~{} tokens, budget {})", level, len(facets), est_tokens, facet_context_budget)
		batches: list[list[dict[str, Any]]] = []
		for i in range(0, len(facets), batch_size):
			batches.append(facets[i : i + batch_size])

		next_level: list[dict[str, Any] | None] = [None] * len(batches)
		multi_batches = [(idx, b) for idx, b in enumerate(batches) if len(b) > 1]
		single_batches = [(idx, b[0]) for idx, b in enumerate(batches) if len(b) == 1]

		for idx, facet in single_batches:
			next_level[idx] = facet

		if max_workers > 1 and len(multi_batches) > 1:
			effective_workers = min(max_workers, len(multi_batches))
			with ThreadPoolExecutor(max_workers=effective_workers) as pool:
				merge_futures = {
					pool.submit(_merge_facet_batch, batch, batch_idx, level): orig_idx
					for batch_idx, (orig_idx, batch) in enumerate(multi_batches)
				}
				for future in as_completed(merge_futures):
					orig_idx = merge_futures[future]
					next_level[orig_idx] = future.result()
		else:
			for batch_idx, (orig_idx, batch) in enumerate(multi_batches):
				next_level[orig_idx] = _merge_facet_batch(batch, batch_idx, level)

		facets = [f for f in next_level if f is not None]
		if not facets:
			raise RuntimeError(f"reduce_level_{level}_produced_no_facets")
		formatted = _format_facets(facets)
		est_tokens = estimate_tokens(formatted)
		level += 1

	# ── Synthesis: produce final TraceSummaryCandidate from facets ──
	logger.info("Synthesis: {} facets (~{} tokens)", len(facets), est_tokens)
	lms = configure_dspy_lms("summarize")
	synthesizer = dspy.Predict(SynthesizeSummarySignature)
	history_start = len(lms[0].history)
	w_start = time.time()
	_, result = call_with_fallback(
		synthesizer, lms, ordered_facets=formatted,
	)
	logger.info("Synthesis: done ({:.1f}s)", time.time() - w_start)
	capture_dspy_cost(lms[0], history_start)

	payload = result.summary_payload
	if isinstance(payload, TraceSummaryCandidate):
		return payload
	if isinstance(payload, dict):
		return TraceSummaryCandidate.model_validate(payload)
	raise RuntimeError("synthesis_produced_invalid_payload")


def _summarize_trace(
    transcript: str,
    *,
    metadata: dict[str, Any] | None = None,
    guidance: str = "",
) -> dict[str, Any]:
    """Run Predict summarization and return validated summary with metadata merged.

    Single call when trace fits in context. Sequential refine/fold for oversized traces.
    Deterministic fields (date, time, coding_agent, etc.) are merged from metadata
    after the LLM call, not generated by the LLM.
    """
    if not transcript.strip():
        raise RuntimeError("session_trace_empty")
    # Pre-process: convert agent JSONL to clean conversation format.
    # Strips tool outputs, metadata noise, adds [USER]/[ASSISTANT] labels.
    # Typically reduces trace size by 10-12x (e.g. 13MB → 1.1MB).
    if "\n{" in transcript:
        formatted = _format_transcript_for_extraction(transcript)
        if formatted.strip() and formatted != transcript:
            transcript = formatted
    config = get_config()
    max_window_tokens = config.summarize_role.max_window_tokens
    overlap_tokens = config.summarize_role.window_overlap_tokens
    lms = configure_dspy_lms("summarize")
    meta = metadata or {}
    guid = guidance.strip()

    history_start = len(lms[0].history)
    trace_tokens = estimate_tokens(transcript)

    prompt_headroom = 8000  # DSPy signature + XML formatting overhead
    if trace_tokens <= max_window_tokens - prompt_headroom:
        # Fast path: single call — trace fits in context
        logger.info("Summarization: single call ({} est. tokens)", trace_tokens)
        w_start = time.time()
        summarizer = dspy.Predict(TraceSummarySignature)
        _, result = call_with_fallback(
            summarizer, lms, transcript=transcript, guidance=guid
        )
        logger.info("Summarization: done ({:.1f}s)", time.time() - w_start)
    else:
        # Parallel path: MapReduce with tree reduction
        if "\n{" in transcript:
            windows = window_transcript_jsonl(
                transcript, max_window_tokens, overlap_tokens
            )
        else:
            windows = window_transcript(transcript, max_window_tokens, overlap_tokens)

        max_workers = config.summarize_role.max_workers
        w_start = time.time()
        candidate = _map_and_reduce(windows, guid, max_workers)
        logger.info("Summarization: done ({:.1f}s, {} windows)", time.time() - w_start, len(windows))

        # _map_and_reduce returns TraceSummaryCandidate directly — wrap for
        # the unified payload extraction below.
        class _Result:
            summary_payload = candidate
        result = _Result()

    capture_dspy_cost(lms[0], history_start)

    payload = getattr(result, "summary_payload", None)
    if isinstance(payload, TraceSummaryCandidate):
        candidate = payload
    elif isinstance(payload, dict):
        candidate = TraceSummaryCandidate.model_validate(payload)
    else:
        raise RuntimeError("dspy summary_payload must be TraceSummaryCandidate or dict")
    llm_payload = candidate.model_dump(mode="json", exclude_none=True)

    # Merge deterministic metadata fields (not generated by the LLM)
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    llm_payload["date"] = meta.get("date", now_iso[:10])
    llm_payload["time"] = meta.get("time", now_iso[11:19])
    llm_payload["coding_agent"] = meta.get("coding_agent") or meta.get(
        "agent_type", "unknown"
    )
    llm_payload["raw_trace_path"] = meta.get("raw_trace_path", "")
    llm_payload["run_id"] = meta.get("run_id", "")
    llm_payload["repo_name"] = meta.get("repo_name", "")
    return llm_payload


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
    guidance: str = "",
) -> dict[str, Any]:
    """Summarize one session trace file into markdown-ready metadata + <=300 word summary."""
    if not session_file_path.exists() or not session_file_path.is_file():
        raise FileNotFoundError(f"session_file_missing:{session_file_path}")
    transcript = session_file_path.read_text(encoding="utf-8")
    meta = metadata or {}
    meta.setdefault("raw_trace_path", str(session_file_path))
    return _summarize_trace(
        transcript,
        metadata=meta,
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
    parser.add_argument("--guidance", default="")
    args = parser.parse_args()

    if args.trace_path:
        session_file = Path(args.trace_path).expanduser()
        metadata = json.loads(args.metadata_json)
        payload = summarize_trace_from_session_file(
            session_file,
            metadata=metadata if isinstance(metadata, dict) else {},
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
                session_path,
                metadata={
                    "run_id": run_id,
                    "coding_agent": "codex",
                    "repo_name": "lerim",
                },
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
