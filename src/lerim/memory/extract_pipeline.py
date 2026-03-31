"""Extraction pipeline for session transcripts using DSPy modules + windowing.

session file (.jsonl/.json) -> read text -> window (if needed) -> dspy.ChainOfThought
-> deterministic pre-filter -> LLM consolidation -> LLM quality gate.

Traces are compacted by adapters (tool outputs stripped), so most traces fit in a
single window. Windowing is a fallback for unusually large sessions. Post-extraction,
an LLM consolidation pass merges semantic duplicates across windows, and a quality
gate drops low-value candidates before the sync agent sees them.

When max_workers > 1, windows are processed in parallel via ThreadPoolExecutor.
Each thread gets its own DSPy LM instances for thread safety.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import dspy
import logfire

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
    """Extract reusable memory candidates from this coding-agent session transcript.

    HARD GATE — ask for EVERY candidate before including it:
    "If an agent read this memory at the start of a future session on this project,
    would it CONCRETELY change a decision the agent makes?"
    If the answer is "no" or "probably not" — do NOT extract it.

    THE FUTURE-SELF TEST: Only extract items that would help the user or their
    coding agent in a FUTURE session. If the information is obtainable by reading
    the codebase, git log, or documentation, do NOT extract it.

    DO NOT EXTRACT:
    - Facts the assistant learned by READING code, configs, or docs (code-derivable).
    - Implementation details that will be visible in the codebase once committed
      (config values, timeout numbers, CLI commands, tool settings, hook args).
    - Generic programming knowledge any experienced developer already knows
      (debounce inputs, use pre-push hooks, cache API responses).
    - Ephemeral task details (line-number fixes, PR comments, TODO items).
    - Items where the body merely restates the title without adding WHY or HOW.
    - Version-specific changelogs or release notes (git log has these).
    - Raw web search results that were not synthesized into a conclusion.
    - Observations that are self-evident or tautological ("keep high-value items",
      "archive low-value items", "merge duplicates into one").
    - Architecture or workflow descriptions ("the pipeline has 6 steps",
      "extraction runs via DSPy") — these describe WHAT exists, not WHY.
    - Specific numbers that will change soon (eval scores, trace counts,
      timeout values, weight coefficients) unless the RATIONALE is the point.

    EXTRACT (high-value items only):
    - Decisions: choices about how to build, structure, or design things — by the user OR by the
      agent during implementation. If the agent chose an approach and the user didn't object, that
      is a team decision worth remembering. Includes strategic, product, and business decisions.
    - Preferences: coding style, tool preferences, workflow habits (usually user-originated)
    - Hard-won insights: non-obvious lessons learned through debugging or painful experience
    - Friction: recurring blockers, time-wasters, tool failures worth remembering
    - Pitfalls: mistakes to avoid that are NOT obvious from reading the code
    - Procedures: multi-step workarounds that would otherwise be forgotten
    - Research conclusions: when the user explicitly requested research and the session
      produced synthesized findings that inform project direction.

    EXAMPLES — extract vs skip:
    ✓ "Use SQLite for the session catalog" → decision (agent CHOSE an approach, WHY matters)
    ✓ "use tabs for indentation" → preference (user STATED)
    ✓ "vllm-mlx crashes with concurrent Metal requests" → pitfall (DISCOVERED through debugging)
    ✓ "Restrictive extraction rules always backfire — 5 experiments ALL regressed" → insight (QUANTIFIED and NON-OBVIOUS)
    ✓ "don't mock the database in tests" → preference. WHY: mocked tests passed but prod migration failed.
    ✗ "The config file has sync_interval = 10" → REPORTED a config value (code-derivable)
    ✗ "The extraction pipeline uses DSPy Predict" → DESCRIBED existing code (code-derivable)
    ✗ "B2B SaaS typically converts at 5-7%" → UNSOLICITED generic statistic
    ✗ "Debounce search input by 300ms" → IMPLEMENTATION DETAIL readable from code
    ✗ "Pre-commit hook needs --hook-type pre-push" → TOOL CONFIG, belongs in docs
    ✗ "Use timeout 2400 for eval with 327 traces" → EPHEMERAL NUMBERS that will change
    ✗ "Eval formula v1 was wrong" → DEAD CODE, v1 no longer exists
    ✗ "Accept empty cross-session analysis" → OBVIOUS, wouldn't change any behavior
    ✗ "Merge duplicate topics into comprehensive target" → GENERIC ADVICE, title says it all
    ✗ "Sync workflow processes sessions in 6 steps" → ARCHITECTURE DESCRIPTION, code has this

    QUALITY BAR for each candidate:
    - Actionable: MUST change how an agent behaves in a future session. This is non-negotiable.
    - Atomic: ONE decision or learning per candidate. Don't bundle multiple items.
    - Context-independent: understandable without the original conversation.
    - Structured body: the body must add information NOT present in the title.
      Lead with the rule/fact, then WHY, then HOW TO APPLY.
      Target: 2-4 sentences. The reader is an expert programmer — focus on the non-obvious WHY.
    - Durable: still relevant weeks or months later, not tied to a specific moment.

    Kind (for learnings only):
    - insight: a reusable observation or pattern
    - procedure: a step-by-step fix or workflow
    - friction: a blocker, struggle, or time-waster
    - pitfall: a mistake to avoid
    - preference: a user preference, habit, convention, or style choice

    CRITICAL — primitive vs kind:
    - primitive MUST be exactly the string "decision" or "learning" and nothing else.
    - Never put insight, procedure, friction, pitfall, or preference in primitive.
    - Those subtype names belong in the kind field only when primitive is "learning".

    Confidence calibration:
    - 0.9+ = the user or agent EXPLICITLY stated this verbatim.
             Only for direct quotes like "I decided X" or "always use Y".
    - 0.75-0.85 = strongly implied by consistent behavior across multiple turns,
                   or agent chose and user accepted without objection.
    - 0.55-0.70 = inferred from a single turn. Reasonable interpretation but unconfirmed.
    - 0.3-0.5 = weak signal. Only extract if the topic is highly unusual or novel.
    - Below 0.3 = do not extract.
    SELF-CHECK: If you assigned 0.8+ to more than half your candidates, re-examine
    each and ask: "Did the user literally say this, or am I interpreting?"

    Durability calibration:
    - permanent: user preferences, identity, and convictions (about the person)
    - project: architecture decisions with rationale NOT in code (the WHY behind a choice)
    - session: specific numbers, eval results, current scores, version-specific bugs,
      tool config values, CLI commands (things about THIS moment)

    Prefer precision over recall. Fewer high-quality items beat many weak ones.
    If a session has no durable memories, return an empty list: [].

    Title format: Start with a verb phrase ("Use X for Y") or noun phrase ("X configuration").
    Make titles specific and self-contained — someone reading just the title should understand
    the core decision or learning without needing the body.
    """

    transcript: str = dspy.InputField(
        desc="Raw session transcript text (JSONL or JSON, schema varies by agent)"
    )
    guidance: str = dspy.InputField(
        desc="Optional lead-agent natural language guidance about focus areas, trace context, and dedupe hints"
    )
    primitives: list[MemoryCandidate] = dspy.OutputField(
        desc='List of memory candidates. Each item: primitive is only "decision" or "learning". '
        'Learning subtypes (pitfall, insight, preference, procedure, friction) go in kind, not primitive. '
        'If no candidates, return [].'
    )


def _is_tautological(title: str, body: str) -> bool:
    """Check if body merely restates the title."""
    t = title.lower().strip(".")
    b = body.lower().strip(".")
    if t == b:
        return True
    # Body starts with title and adds < 20 chars of substance
    if b.startswith(t) and len(body) < len(title) + 20:
        return True
    return False


def _filter_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply deterministic quality gates after DSPy extraction, before persist.

    Removes candidates that fail basic quality checks. Runs after
    DSPy extraction, before writing to extract.json.
    """
    filtered = []
    for item in candidates:
        title = str(item.get("title") or "").strip()
        body = str(item.get("body") or "").strip()
        confidence = item.get("confidence")
        durability = str(item.get("durability") or "project")

        # Gate 1: Drop low-confidence (< 0.3)
        if isinstance(confidence, (int, float)) and confidence < 0.3:
            continue
        # Gate 2: Title too short (< 10 chars)
        if len(title) < 10:
            continue
        # Gate 3: Body too thin (< 50 chars) — must add substance beyond title
        if len(body) < 50:
            continue
        # Gate 4: Tautological (body ≈ title)
        if _is_tautological(title, body):
            continue
        # Gate 5: Session-durability items dropped
        if durability == "session":
            continue
        # Gate 6: Learnings must carry a valid kind
        primitive = str(item.get("primitive") or "").strip()
        kind = str(item.get("kind") or "").strip()
        if primitive == "learning" and kind not in {
            "insight", "procedure", "friction", "pitfall", "preference"
        }:
            continue

        # Normalize tags: lowercase, hyphenated, deduplicated.
        tags = item.get("tags", [])
        if isinstance(tags, list):
            item["tags"] = sorted(set(
                t.strip().lower().replace(" ", "-")
                for t in tags if t and t.strip()
            ))

        filtered.append(item)
    return filtered


class ConsolidateCandidatesSignature(dspy.Signature):
	"""Merge near-duplicate memory candidates extracted from overlapping transcript windows.

	You receive candidates from overlapping windows of the SAME session. Adjacent
	windows share ~20% text overlap, so the same insight may appear in different
	wording across windows.

	For each group of semantic duplicates:
	- Keep the version with the richest, most structured body (WHY + HOW TO APPLY)
	- Use the highest confidence score from the group
	- Union all tags from duplicates
	- If source_speaker differs across duplicates, use "both"
	- If durability differs, keep the more durable (permanent > project > session)
	- Preserve the most specific title

	Candidates covering DIFFERENT topics must remain separate — do not merge
	unrelated items. If no duplicates exist, return the input unchanged.
	"""

	candidates: list[MemoryCandidate] = dspy.InputField(
		desc="All memory candidates extracted from overlapping transcript windows of one session",
	)
	unique_candidates: list[MemoryCandidate] = dspy.OutputField(
		desc="Deduplicated set — duplicates merged, unique items unchanged. Same MemoryCandidate schema.",
	)


class QualityGateSignature(dspy.Signature):
	"""Filter memory candidates to keep only high-quality, durable items worth persisting.

	HARD GATES (fail ANY one → DROP immediately):
	- NOT actionable: would not concretely change how an agent behaves in a future session.
	  Ask: "What would an agent do DIFFERENTLY after reading this?" If no clear answer → DROP.
	- Code-derivable: the information exists in the codebase, git log, docs, or config files.
	- Generic knowledge: any experienced programmer already knows this.
	- Self-evident / tautological: the observation is obvious and the body just restates the title.

	SOFT CRITERIA (fail 2+ → DROP):
	1. Atomic: covers ONE decision or learning, not bundled
	2. Context-independent: understandable without the original conversation
	3. Structured body: adds WHY and HOW beyond what the title says
	4. Durable: still relevant weeks or months later, not tied to specific numbers or versions
	5. Information-dense: body adds substance, not just rephrasing the title

	DROP examples:
	- "Debounce search input by 300ms" → code-derivable config value
	- "Accept empty cross-session analysis" → self-evident, changes nothing
	- "Sync workflow has 6 steps" → architecture description, read the code
	- "Use timeout 2400 for 327 traces" → ephemeral numbers, will change
	- "Merge duplicates into comprehensive target" → generic advice, obvious

	KEEP examples:
	- "Restrictive extraction rules always backfire" → non-obvious, quantified, changes approach
	- "Replace app-level sandboxing with Docker kernel isolation" → architecture WHY not in code

	Do NOT rewrite or modify candidates. Return accepted candidates exactly as
	received. This is a filter, not a rewriter.
	"""

	candidates: list[MemoryCandidate] = dspy.InputField(
		desc="Consolidated memory candidates to evaluate for quality",
	)
	accepted: list[MemoryCandidate] = dspy.OutputField(
		desc="High-quality candidates that pass all criteria. Subset of input, unmodified.",
	)


class MemoryExtractionPipeline(dspy.Module):
	"""Three-stage memory extraction: extract → consolidate → quality gate.

	Stage 1 (extract): Run per transcript window, produces raw candidates.
	Stage 2 (consolidate): Merge semantic duplicates across overlapping windows.
	Stage 3 (quality_gate): Drop low-quality candidates using LLM judgment.

	All three stages are optimizable by DSPy (MIPROv2, BootstrapFewShot, etc.).
	"""

	def __init__(self):
		super().__init__()
		self.extract = dspy.ChainOfThought(MemoryExtractSignature)
		self.consolidate = dspy.ChainOfThought(ConsolidateCandidatesSignature)
		self.quality_gate = dspy.ChainOfThought(QualityGateSignature)

	def forward(self, windows: list[str], guidance: str = "") -> dspy.Prediction:
		# Stage 1: Extract from each window
		all_candidates: list[dict[str, Any]] = []
		for window in windows:
			result = self.extract(transcript=window, guidance=guidance)
			primitives = result.primitives
			if isinstance(primitives, list):
				for item in primitives:
					if isinstance(item, MemoryCandidate):
						all_candidates.append(item.model_dump(mode="json", exclude_none=True))
					elif isinstance(item, dict):
						all_candidates.append(item)

		# Deterministic pre-filter (cheap, catches obvious junk)
		filtered = _filter_candidates(all_candidates)
		if not filtered:
			return dspy.Prediction(primitives=[])

		# Stage 2: Consolidate cross-window duplicates
		if len(filtered) > 1:
			result = self.consolidate(candidates=filtered)
			unique = result.unique_candidates
			if isinstance(unique, list) and unique:
				filtered = _to_dicts(unique)

		# Stage 3: Quality gate
		result = self.quality_gate(candidates=filtered)
		accepted = result.accepted
		if isinstance(accepted, list):
			return dspy.Prediction(primitives=_to_dicts(accepted))

		return dspy.Prediction(primitives=filtered)


def _to_dicts(items: list) -> list[dict[str, Any]]:
	"""Normalize MemoryCandidate or dict items to plain dicts."""
	result: list[dict[str, Any]] = []
	for item in items:
		if isinstance(item, MemoryCandidate):
			result.append(item.model_dump(mode="json", exclude_none=True))
		elif isinstance(item, dict):
			result.append(item)
	return result


def _consolidate_and_gate(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
	"""Run LLM consolidation and quality gate on extracted candidates.

	Stage 2: Merge semantic duplicates across overlapping windows.
	Stage 3: Drop low-quality candidates using LLM judgment.

	Falls back gracefully — if either stage fails, the previous result is kept.
	"""
	if not candidates:
		return []

	lms = configure_dspy_lms("extract")
	pipeline = MemoryExtractionPipeline()

	# Stage 2: Consolidate cross-window duplicates
	if len(candidates) > 1:
		with logfire.span("consolidate_candidates", count=len(candidates)):
			pre_count = len(candidates)
			history_start = len(lms[0].history)
			try:
				_, result = call_with_fallback(
					pipeline.consolidate, lms, candidates=candidates,
				)
				unique = result.unique_candidates
				if isinstance(unique, list) and unique:
					candidates = _to_dicts(unique)
				logger.info("Consolidation: {} → {} candidates", pre_count, len(candidates))
			except Exception:
				logger.warning("Consolidation failed, keeping {} pre-filtered candidates", pre_count)
			finally:
				capture_dspy_cost(lms[0], history_start)

	# Stage 3: Quality gate
	with logfire.span("quality_gate", count=len(candidates)):
		history_start = len(lms[0].history)
		try:
			_, result = call_with_fallback(
				pipeline.quality_gate, lms, candidates=candidates,
			)
			accepted = result.accepted
			if isinstance(accepted, list):
				candidates = _to_dicts(accepted)
			logger.info("Quality gate: {} accepted", len(candidates))
		except Exception:
			logger.warning("Quality gate failed, keeping {} consolidated candidates", len(candidates))
		finally:
			capture_dspy_cost(lms[0], history_start)

	return candidates


def _format_transcript_for_extraction(raw: str) -> str:
	"""Convert compacted JSONL transcript to clean conversation format for extraction.

	Supports 4 agent formats: Claude, OpenCode, Codex, Cursor.
	Strips metadata noise, clears tool inputs, adds [USER]/[ASSISTANT] speaker labels.
	Returns plain text conversation — not JSONL.
	"""
	lines_parsed: list[dict] = []
	for line in raw.split("\n"):
		line = line.strip()
		if not line:
			continue
		try:
			obj = json.loads(line)
			if isinstance(obj, dict):
				lines_parsed.append(obj)
		except (json.JSONDecodeError, ValueError):
			continue

	if not lines_parsed:
		return raw  # fallback: return as-is if no JSON found

	fmt = _detect_trace_format(lines_parsed)
	formatter = {
		"claude": _format_claude_line,
		"opencode": _format_opencode_line,
		"codex": _format_codex_line,
		"cursor": _format_cursor_line,
	}.get(fmt)

	if not formatter:
		return raw  # unknown format, pass through

	parts: list[str] = []
	for obj in lines_parsed:
		result = formatter(obj)
		if result:
			parts.append(result)

	formatted = "\n\n".join(parts)
	return formatted + "\n" if formatted else raw


def _detect_trace_format(lines: list[dict]) -> str:
	"""Detect which agent produced this trace by inspecting line structure."""
	for obj in lines[:5]:  # check first 5 lines
		# Claude: has "type" in ("user","assistant","human") and "message" key
		if obj.get("type") in ("user", "assistant", "human") and "message" in obj:
			return "claude"
		# Codex: has "type" in ("event_msg","response_item","session_meta")
		if obj.get("type") in ("event_msg", "response_item", "session_meta"):
			return "codex"
		# Cursor: has "_v" key and integer "type"
		if "_v" in obj and isinstance(obj.get("type"), int):
			return "cursor"
		# OpenCode: has "role" at top level (not nested in message)
		if "role" in obj and "message" not in obj:
			return "opencode"
		# OpenCode metadata line (first line)
		if "session_id" in obj:
			continue  # skip metadata, check next line
	return "unknown"


def _format_claude_line(obj: dict) -> str | None:
	"""Format one Claude compacted JSONL line."""
	entry_type = obj.get("type", "")
	msg = obj.get("message", {})
	if not isinstance(msg, dict):
		return None

	role = msg.get("role", entry_type)
	content = msg.get("content")

	if role in ("user", "human"):
		text = _extract_content_text(content, skip_tool_results=True)
		if text:
			return f"[USER]\n{text}"
	elif role in ("assistant", "ai"):
		# "ai" is used by some LangChain-style traces
		text = _extract_content_text(content, skip_tool_results=False)
		if text:
			return f"[ASSISTANT]\n{text}"

	return None


def _format_opencode_line(obj: dict) -> str | None:
	"""Format one OpenCode compacted JSONL line."""
	# Skip metadata line
	if "session_id" in obj and "role" not in obj:
		return None

	role = obj.get("role", "")
	content = obj.get("content", "")

	if role == "user":
		text = str(content).strip() if content else ""
		if text:
			return f"[USER]\n{text}"
	elif role == "assistant":
		text = str(content).strip() if content else ""
		if text:
			return f"[ASSISTANT]\n{text}"
	elif role == "tool":
		tool_name = obj.get("tool_name", "tool")
		tool_input = obj.get("tool_input", {})
		summary = _summarize_tool_use(tool_name, tool_input)
		return f"[TOOL]\n{summary}"

	return None


def _format_codex_line(obj: dict) -> str | None:
	"""Format one Codex compacted JSONL line."""
	entry_type = obj.get("type", "")
	payload = obj.get("payload", {})
	if not isinstance(payload, dict):
		return None

	# Skip metadata
	if entry_type == "session_meta":
		return None

	payload_type = payload.get("type", "")

	# User message
	if payload_type == "user_message" or (entry_type == "event_msg" and payload_type == "user_message"):
		text = str(payload.get("message", "")).strip()
		if text:
			return f"[USER]\n{text}"

	# Assistant message content
	role = payload.get("role", "")
	if role == "assistant" or payload_type in ("agent_message", "message"):
		content = payload.get("content")
		text = _extract_content_text(content, skip_tool_results=False)
		if text:
			return f"[ASSISTANT]\n{text}"

	# Function call
	if payload_type == "function_call":
		name = payload.get("name", "tool")
		args_raw = payload.get("arguments", "{}")
		try:
			args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
		except (json.JSONDecodeError, ValueError):
			args = {}
		summary = _summarize_tool_use(name, args if isinstance(args, dict) else {})
		return f"[TOOL]\n{summary}"

	# Custom tool call
	if payload_type == "custom_tool_call":
		name = payload.get("name", "tool")
		input_data = payload.get("input", {})
		summary = _summarize_tool_use(name, input_data if isinstance(input_data, dict) else {})
		return f"[TOOL]\n{summary}"

	# Skip function_call_output (already cleared)
	if payload_type == "function_call_output":
		return None

	return None


def _format_cursor_line(obj: dict) -> str | None:
	"""Format one Cursor compacted JSONL line."""
	# Skip metadata line (has composerId but no bubbleId)
	if "composerId" in obj and "bubbleId" not in obj:
		return None

	bubble_type = obj.get("type")
	if not isinstance(bubble_type, int):
		return None

	text = str(obj.get("text", "")).strip()

	# Type 1 = user
	if bubble_type == 1:
		if text:
			return f"[USER]\n{text}"
		return None

	# Type 2 = assistant
	if bubble_type == 2:
		parts = []
		if text:
			parts.append(text)

		# Tool uses from toolFormerData
		tool_data = obj.get("toolFormerData")
		if isinstance(tool_data, list):
			for td in tool_data:
				if isinstance(td, dict):
					name = td.get("name", "tool")
					params_raw = td.get("params", "{}")
					try:
						params = json.loads(params_raw) if isinstance(params_raw, str) else params_raw
					except (json.JSONDecodeError, ValueError):
						params = {}
					summary = _summarize_tool_use(name, params if isinstance(params, dict) else {})
					parts.append(summary)
		elif isinstance(tool_data, dict):
			name = tool_data.get("name", "tool")
			params_raw = tool_data.get("params", "{}")
			try:
				params = json.loads(params_raw) if isinstance(params_raw, str) else params_raw
			except (json.JSONDecodeError, ValueError):
				params = {}
			summary = _summarize_tool_use(name, params if isinstance(params, dict) else {})
			parts.append(summary)

		if parts:
			return "[ASSISTANT]\n" + "\n".join(parts)
		return None

	# Type 30 = thinking, skip
	return None


def _extract_content_text(content, *, skip_tool_results: bool = False) -> str:
	"""Extract readable text from content (string or list of blocks).

	Handles Claude/Codex content format: string or list of
	{type: "text", text: "..."} / {type: "tool_use", ...} / {type: "tool_result", ...} blocks.
	"""
	if isinstance(content, str):
		return content.strip()

	if not isinstance(content, list):
		return ""

	texts: list[str] = []
	for block in content:
		if not isinstance(block, dict):
			continue
		btype = block.get("type", "")

		if btype == "text":
			t = str(block.get("text", "")).strip()
			if t:
				texts.append(t)

		elif btype == "tool_use":
			name = block.get("name", "tool")
			input_data = block.get("input", {})
			summary = _summarize_tool_use(name, input_data if isinstance(input_data, dict) else {})
			texts.append(summary)

		elif btype == "tool_result" and skip_tool_results:
			continue  # skip cleared tool results

		elif btype == "thinking":
			continue  # skip thinking blocks entirely

	return "\n".join(texts)


def _summarize_tool_use(name: str, input_data: dict) -> str:
	"""One-line summary of a tool use without full content."""
	if not isinstance(input_data, dict):
		return f"[Used {name}]"

	# File tools: show just the filename
	# Covers: file_path (Claude/Codex), path (generic), filePath (OpenCode),
	# targetFile (Cursor read_file_v2), relativeWorkspacePath (Cursor edit_file_v2)
	for key in ("file_path", "path", "filePath", "targetFile", "relativeWorkspacePath"):
		path = input_data.get(key, "")
		if path:
			short = str(path).rsplit("/", 1)[-1] if "/" in str(path) else str(path)
			return f"[Used {name} on {short}]"

	# Bash/shell: show truncated command
	cmd = input_data.get("command", "")
	if cmd:
		short_cmd = str(cmd)[:80] + ("..." if len(str(cmd)) > 80 else "")
		return f"[Ran: {short_cmd}]"

	# Search tools: show query
	# Covers: query, pattern (grep/glob), prompt (agent delegation), globPattern (Cursor)
	for key in ("query", "pattern", "prompt", "globPattern"):
		q = input_data.get(key, "")
		if q:
			short_q = str(q)[:60] + ("..." if len(str(q)) > 60 else "")
			return f"[Used {name}: {short_q}]"

	# Task/agent delegation: show description
	desc = input_data.get("description", "")
	if desc:
		short_d = str(desc)[:60] + ("..." if len(str(desc)) > 60 else "")
		return f"[Used {name}: {short_d}]"

	return f"[Used {name}]"


def _is_empty_primitives_parse_error(exc: RuntimeError) -> bool:
    """Detect when all LMs failed because the model returned empty primitives.

    This happens when the LLM correctly determines no memories exist but
    returns <primitives></primitives> instead of <primitives>[]</primitives>.
    """
    cause = exc.__cause__
    if cause is None:
        return False
    from dspy.utils.exceptions import AdapterParseError

    if not isinstance(cause, AdapterParseError):
        return False
    response = getattr(cause, "lm_response", "") or ""
    return bool(re.search(r"<primitives>\s*</primitives>", response))


def _extract_one_window(
    wi: int,
    total: int,
    window: str,
    guidance: str,
) -> list[dict[str, Any]]:
    """Extract candidates from a single window with its own LM instances.

    Each call creates fresh DSPy LM instances so parallel threads never
    share mutable history state.
    """
    with logfire.span("extract_window", window_index=wi, total_windows=total):
        lms = configure_dspy_lms("extract")
        extractor = dspy.ChainOfThought(MemoryExtractSignature)
        history_start = len(lms[0].history)
        logger.info("  Window {}/{}: extracting...", wi, total)
        w_start = time.time()
        try:
            _, result = call_with_fallback(extractor, lms, transcript=window, guidance=guidance)
        except RuntimeError as exc:
            if _is_empty_primitives_parse_error(exc):
                logger.info("  Window {}/{}: no candidates (empty primitives)", wi, total)
                capture_dspy_cost(lms[0], history_start)
                return []
            raise
        candidates: list[dict[str, Any]] = []
        primitives = getattr(result, "primitives", [])
        if isinstance(primitives, list):
            for item in primitives:
                if isinstance(item, MemoryCandidate):
                    candidates.append(item.model_dump(mode="json", exclude_none=True))
                elif isinstance(item, dict):
                    candidates.append(item)
        logger.info(
            "  Window {}/{}: done ({:.1f}s, {} candidates)",
            wi,
            total,
            time.time() - w_start,
            len(primitives) if isinstance(primitives, list) else 0,
        )
        capture_dspy_cost(lms[0], history_start)
        return candidates


def _extract_candidates(
    transcript: str,
    *,
    guidance: str = "",
) -> list[dict[str, Any]]:
    """Run Predict extraction with windowing and return normalized candidates.

    When max_workers > 1 and multiple windows exist, processes windows in
    parallel via ThreadPoolExecutor. Otherwise falls back to sequential.
    Applies deterministic filtering, LLM consolidation, and LLM quality gate.
    """
    if not transcript.strip():
        return []
    # Pre-process: convert agent JSONL to clean conversation format
    if "\n{" in transcript:
        formatted = _format_transcript_for_extraction(transcript)
        if formatted.strip() and formatted != transcript:
            transcript = formatted
    config = get_config()
    max_window_tokens = config.extract_role.max_window_tokens
    overlap_tokens = config.extract_role.window_overlap_tokens
    max_workers = config.extract_role.max_workers
    if "\n{" in transcript:
        windows = window_transcript_jsonl(transcript, max_window_tokens, overlap_tokens)
    else:
        windows = window_transcript(transcript, max_window_tokens, overlap_tokens)

    with logfire.span("extract_candidates", windows=len(windows), max_workers=max_workers):
        logger.info(
            "Extraction: {} window(s), max_window_tokens={}, max_workers={}",
            len(windows),
            max_window_tokens,
            max_workers,
        )
        guid = guidance.strip()
        total = len(windows)

        if max_workers > 1 and total > 1:
            # Parallel: each thread gets its own LM instances via _extract_one_window
            effective_workers = min(max_workers, total)
            logger.info("Extraction: parallel mode ({} workers)", effective_workers)
            all_candidates: list[dict[str, Any]] = []
            with ThreadPoolExecutor(max_workers=effective_workers) as pool:
                futures = {
                    pool.submit(_extract_one_window, wi, total, window, guid): wi
                    for wi, window in enumerate(windows, 1)
                }
                for future in as_completed(futures):
                    all_candidates.extend(future.result())
            return _consolidate_and_gate(_filter_candidates(all_candidates))

        # Sequential: single-thread path (max_workers=1 or single window)
        all_candidates = []
        for wi, window in enumerate(windows, 1):
            all_candidates.extend(_extract_one_window(wi, total, window, guid))
        return _consolidate_and_gate(_filter_candidates(all_candidates))


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

        # --- Filter function tests ---
        assert _is_tautological("Some title", "Some title") is True
        assert _is_tautological("Some title", "Some title.") is True
        assert _is_tautological("Short title", "Short title plus tiny bit") is True
        assert _is_tautological(
            "Good memory title",
            "Good memory title with substantial additional information that adds real value",
        ) is False
        assert _is_tautological("A", "Completely different body text") is False

        unfiltered = [
            {"title": "Short", "body": "Short.", "confidence": 0.3},  # low conf + short
            {"title": "Tautology test item", "body": "Tautology test item", "confidence": 0.8},  # tautological
            {"title": "Ephemeral detail here", "body": "Fix line 42 in the PR review for this issue.", "confidence": 0.7, "durability": "session"},  # session
            {"title": "Good memory about architecture", "body": "Use event sourcing for the queue system to enable replay and debugging.", "confidence": 0.8, "durability": "project"},  # should survive
        ]
        filtered = _filter_candidates(unfiltered)
        assert len(filtered) == 1, f"Expected 1 survivor, got {len(filtered)}: {filtered}"
        assert filtered[0]["title"] == "Good memory about architecture"
        print("extract_pipeline: filter self-tests passed")

        # --- Transcript formatting tests ---
        fixtures_dir = Path(__file__).parent.parent.parent.parent / "tests" / "fixtures" / "traces"

        # Test Claude formatting
        claude_short = fixtures_dir / "claude_short.jsonl"
        if claude_short.exists():
            raw = claude_short.read_text(encoding="utf-8")
            formatted = _format_transcript_for_extraction(raw)
            assert "[USER]" in formatted or "[ASSISTANT]" in formatted, "Claude: no speaker labels found"
            assert "input_tokens" not in formatted, "Claude: metadata not stripped"
            assert "cache_creation_input_tokens" not in formatted, "Claude: cache metadata not stripped"
            assert "stop_reason" not in formatted, "Claude: stop_reason not stripped"
            # Check token reduction
            reduction = 1 - len(formatted) / len(raw)
            print(f"  Claude short: {len(raw)} -> {len(formatted)} chars ({reduction:.0%} reduction)")

        claude_rich = fixtures_dir / "claude_rich.jsonl"
        if claude_rich.exists():
            raw = claude_rich.read_text(encoding="utf-8")
            formatted = _format_transcript_for_extraction(raw)
            assert "[USER]" in formatted, "Claude rich: no USER labels"
            assert "[ASSISTANT]" in formatted, "Claude rich: no ASSISTANT labels"
            assert "inference_geo" not in formatted, "Claude rich: metadata not stripped"
            reduction = 1 - len(formatted) / len(raw)
            print(f"  Claude rich: {len(raw)} -> {len(formatted)} chars ({reduction:.0%} reduction)")

        # Test OpenCode formatting
        opencode_short = fixtures_dir / "opencode_short.jsonl"
        if opencode_short.exists():
            raw = opencode_short.read_text(encoding="utf-8")
            formatted = _format_transcript_for_extraction(raw)
            assert "session_id" not in formatted, "OpenCode: metadata line not stripped"
            reduction = 1 - len(formatted) / len(raw)
            print(f"  OpenCode short: {len(raw)} -> {len(formatted)} chars ({reduction:.0%} reduction)")

        opencode_rich = fixtures_dir / "opencode_rich.jsonl"
        if opencode_rich.exists():
            raw = opencode_rich.read_text(encoding="utf-8")
            formatted = _format_transcript_for_extraction(raw)
            assert "[USER]" in formatted or "[ASSISTANT]" in formatted, "OpenCode rich: no speaker labels"
            reduction = 1 - len(formatted) / len(raw)
            print(f"  OpenCode rich: {len(raw)} -> {len(formatted)} chars ({reduction:.0%} reduction)")

        # Test Codex formatting
        codex_short = fixtures_dir / "codex_short.jsonl"
        if codex_short.exists():
            raw = codex_short.read_text(encoding="utf-8")
            formatted = _format_transcript_for_extraction(raw)
            print(f"  Codex short: {len(raw)} -> {len(formatted)} chars")

        # Test Cursor formatting
        cursor_short = fixtures_dir / "cursor_short.jsonl"
        if cursor_short.exists():
            raw = cursor_short.read_text(encoding="utf-8")
            formatted = _format_transcript_for_extraction(raw)
            # Cursor uses integer types (1=user, 2=assistant)
            reduction = 1 - len(formatted) / len(raw) if len(raw) > 0 else 0
            print(f"  Cursor short: {len(raw)} -> {len(formatted)} chars ({reduction:.0%} reduction)")

        cursor_rich = fixtures_dir / "cursor_rich.jsonl"
        if cursor_rich.exists():
            raw = cursor_rich.read_text(encoding="utf-8")
            formatted = _format_transcript_for_extraction(raw)
            reduction = 1 - len(formatted) / len(raw) if len(raw) > 0 else 0
            print(f"  Cursor rich: {len(raw)} -> {len(formatted)} chars ({reduction:.0%} reduction)")

        # Test synthetic format detection
        assert _detect_trace_format([{"type": "user", "message": {"role": "user"}}]) == "claude"
        assert _detect_trace_format([{"role": "user", "content": "hi"}]) == "opencode"
        assert _detect_trace_format([{"type": "event_msg", "payload": {}}]) == "codex"
        assert _detect_trace_format([{"_v": 3, "type": 1, "text": "hi"}]) == "cursor"
        assert _detect_trace_format([{"random": "data"}]) == "unknown"

        print("extract_pipeline: formatting self-tests passed")
