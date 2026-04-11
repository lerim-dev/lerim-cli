"""Three-pass PydanticAI memory extraction pipeline.

Replaces the former DSPy ReAct ExtractAgent. Runs three specialized passes
against a session trace to produce memory files, a session summary, and an
updated index:

1. **Reflect** — read-only pass that scans existing memories, greps for explicit
   "remember" requests, pages through the trace, and produces a structured
   `SessionUnderstanding` (no writes). Uses `read`, `grep`, `scan` tools.

2. **Extract** — takes the `SessionUnderstanding` from pass 1, reads relevant
   trace chunks by evidence offset, dedups against existing memories, and
   writes novel memories or edits existing ones. Uses the same read tools plus
   `write` and `edit`.

3. **Finalize** — verifies `index.md` consistency (and fixes any mismatches via
   `edit`), then writes the session summary via `write(type="summary", ...)`.
   Uses `read`, `scan`, `verify_index`, `edit`, `write`.

Shared infrastructure (single source of truth, reused by `extract_pydanticai.py`
single-pass baseline):
- `ExtractDeps` dataclass and the six PydanticAI tool functions (`read`,
  `grep`, `scan`, `write`, `edit`, `verify_index`) are imported directly
  from `lerim.agents.tools` — one function per tool, no wrappers.
- Three Agent builders + the pipeline runner

Empty-trace short-circuit: after Pass 1, if the `SessionUnderstanding` carries
no extractable candidates AND no relevant existing memories, Pass 2 and Pass 3
are skipped. A minimal "no memories extracted" summary is written via Python.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.usage import UsageLimits

from lerim.agents.tools import (
	ExtractDeps,
	edit,
	grep,
	read,
	scan,
	verify_index,
	write,
)


# ── Pydantic output models ───────────────────────────────────────────────


class ChunkRef(BaseModel):
	"""Pointer to an interesting region of the trace."""

	offset: int = Field(description="Trace line offset where this topic appears")
	topic: str = Field(description="Short description of what's at this offset")


class CandidateMemory(BaseModel):
	"""A single extractable memory identified during reflection."""

	type: Literal["user", "feedback", "project", "reference"] = Field(
		description="Memory type — drives filename prefix and body format"
	)
	topic: str = Field(description="Short topic/title for the memory")
	evidence_offset: int = Field(description="Trace line where evidence appears")


class SessionUnderstanding(BaseModel):
	"""Pass 1 output. Transient — NOT persisted as a memory file."""

	user_goal: str = Field(description="What the user was trying to achieve this session")
	key_decisions: list[str] = Field(description="Durable decisions or conclusions from the session")
	important_chunks: list[ChunkRef] = Field(description="Pointers to interesting trace regions")
	extractable_candidates: list[CandidateMemory] = Field(
		description="Memory candidates identified for Pass 2 to write"
	)
	existing_memories_relevant: list[str] = Field(
		description="Filenames of existing memories relevant to this session (for dedup in Pass 2)"
	)


class ExtractResult(BaseModel):
	"""Pass 2 output. Summarizes what Pass 2 wrote."""

	filenames: list[str] = Field(description="Memory filenames written in this session")
	completion_summary: str = Field(description="Short plain-text summary of Pass 2 actions")


class FinalizeResult(BaseModel):
	"""Pass 3 output. Final pipeline return value."""

	completion_summary: str = Field(description="Short plain-text completion summary for the whole pipeline")
	index_ok: bool = Field(description="True if index.md is consistent after Pass 3")
	summary_filename: str = Field(description="Filename of the session summary written in Pass 3")


# ── System prompts (one per pass) ────────────────────────────────────────


REFLECT_SYSTEM_PROMPT = """\
You are the Lerim memory reflection agent. This is Pass 1 of a three-pass
memory extraction pipeline. Your job is to READ the session and produce a
structured SessionUnderstanding for the extractor. You do NOT write memories
in this pass.

DO NOT WRITE MEMORIES. You only have read-only tools: read, grep,
scan. There is no write or edit in your toolset. Your output is
a SessionUnderstanding object with user_goal, key_decisions, important_chunks,
extractable_candidates, and existing_memories_relevant.

STEPS:

1. ORIENT: Call read("index.md") ONCE. index.md is the authoritative summary
   of ALL existing memories — it lists every memory file with its title,
   description, and semantic category (User Preferences, Project State,
   Feedback, References, etc.). Do NOT also call scan() here — scan("")
   returns the same filenames and descriptions that index.md already lists,
   so calling both wastes a tool turn for zero new information. Only fall
   back to scan("") if read("index.md") returns an error or the file looks
   malformed.

2. FIND EXPLICIT REMEMBER REQUESTS: Call grep("trace", "remember") to
   locate any explicit user requests to remember or store something. Also try
   grep("trace", "memorize") and grep("trace", "keep in mind").

3. CHUNKED READ: read("trace") is hard-capped at 100 lines per call.
   You MUST page through the trace by incrementing offset by 100 each call:
     read("trace", offset=0,   limit=100)  -> lines 1-100
     read("trace", offset=100, limit=100)  -> lines 101-200
     read("trace", offset=200, limit=100)  -> lines 201-300
     ...continue until the header says Y == total lines.
   NEVER re-read the same chunk. Always look at the header
   "[N lines, showing A-B]" and use offset=B on the next call.

4. IDENTIFY EXTRACTABLE CANDIDATES as you read each chunk. Use these criteria:
   - Extract: user role, goals, preferences, working style (about the person)
   - Extract: feedback corrections ("don't do X") AND confirmations ("yes, exactly")
   - Extract: project decisions, context, constraints NOT in code or git
   - Extract: reference pointers to external systems (dashboards, Linear projects, etc.)
   - Do NOT extract: Code patterns, architecture, file paths, function names
   - Do NOT extract: Git history, recent changes
   - Do NOT extract: Debugging solutions (the fix is in the code)
   - Do NOT extract: Anything in CLAUDE.md or README
   - Do NOT extract: Ephemeral task details, in-progress work
   - Do NOT extract: Generic programming knowledge everyone knows
   For each candidate, note the type, topic, and evidence_offset (trace line).

5. CHECK DEDUP: For each candidate, identify if a relevant existing memory
   exists from step 1. Populate existing_memories_relevant with their filenames.

6. PRODUCE SessionUnderstanding with:
   - user_goal: one sentence about what the user was trying to do
   - key_decisions: durable decisions (decisions, NOT task list)
   - important_chunks: ChunkRef(offset, topic) for interesting regions
   - extractable_candidates: CandidateMemory(type, topic, evidence_offset)
   - existing_memories_relevant: filenames of existing memories relevant to this session

If the trace contains NO extractable content (pure debugging, stale, or empty),
return an empty extractable_candidates list and empty existing_memories_relevant.
Pass 2 and Pass 3 will be skipped automatically for empty understandings.

CRITICAL: You do NOT call write or edit in this pass. Those tools
are not available to you. Your sole output is the SessionUnderstanding.
"""


EXTRACT_SYSTEM_PROMPT = """\
You are the Lerim memory extraction agent. This is Pass 2 of a three-pass
pipeline. You receive a SessionUnderstanding from Pass 1 (as JSON in the
user prompt) and write the actual memory files.

INPUTS: Parse the SessionUnderstanding JSON from your user prompt. It has:
  - user_goal: high-level session goal
  - key_decisions: list of durable decisions
  - important_chunks: ChunkRef(offset, topic) pointers
  - extractable_candidates: CandidateMemory(type, topic, evidence_offset) — what to write
  - existing_memories_relevant: filenames of existing memories (for dedup)

YOUR JOB: For each extractable_candidate, read the evidence at its
evidence_offset from the trace, dedup against existing memories, and either
write a new memory or edit an existing one.

FORMAT RULES (critical):

Memory files are named {type}_{topic}.md (e.g. feedback_use_tabs.md,
project_dspy_migration.md). The type is encoded in the filename.
Each file has YAML frontmatter (name, description, type) and a markdown body.

Body format for feedback/project memories — use inline bold, NOT ## headings:
  State the rule or decision first (one line).
  **Why:** reason it matters.
  **How to apply:** concrete action for future sessions.
Example:
  Use tabs for indentation in all code files.
  **Why:** team convention; spaces were rejected in code review.
  **How to apply:** set indent_style=tab; flag spaces in PRs.
DO NOT use ## headings inside the body — headings are only for summaries.

Project memories must lead with the fact or decision, not narrate what happened.
Bad: "## What Happened\\nWe decided to use Redis..." — this is summary style.
Good: "Redis chosen as cache layer (replaced Memcached).\\n**Why:** ..."

CRITICAL RULE: If the user explicitly asks to remember, memorize, store, or
"keep in mind" something (check the SessionUnderstanding for such candidates),
you MUST call write() for that content (usually type user or feedback)
or if exists, edit(). This overrides all skip rules below. Do not treat
explicit requests as debugging or ephemeral.

Duplicates are worse than gaps -- skip when uncertain.
An empty session (no memories written) is valid only when nothing in the
critical rule applies and there is no durable signal in the trace.

EXTRACTION CRITERIA (applied to each candidate before writing):
- Extract: user role, goals, preferences, working style (about the person)
- Extract: feedback corrections ("don't do X") AND confirmations ("yes, exactly")
- Extract: project decisions, context, constraints NOT in code or git
- Extract: reference pointers to external systems (dashboards, Linear projects, etc.)
- Do NOT extract: Code patterns, architecture, file paths, function names, module names
- Do NOT extract: Git history, recent changes
- Do NOT extract: Debugging solutions
- Do NOT extract: Anything in CLAUDE.md or README
- Do NOT extract: Ephemeral task details, in-progress work
- Do NOT extract: Generic programming knowledge everyone knows

STEPS:

1. For each candidate in extractable_candidates, read the trace near its
   evidence_offset via read("trace", offset=X, limit=100). You may also
   read("trace", offset=N, limit=100) on important_chunks for context.

2. DEDUP: Use existing_memories_relevant from the SessionUnderstanding — Pass 1
   already scanned the memory root and listed the filenames relevant to this
   session. Do NOT call scan() here — it returns the same filenames you
   already have in your input and wastes a tool turn. For each candidate,
   read() the SPECIFIC potentially-related memory by filename
   (e.g. read("feedback_tabs.md")) and compare. If the existing memory
   already covers the same topic, skip or edit; do not write a duplicate.

3. WRITE: For each novel memory, call:
     write(type="user"|"feedback"|"project"|"reference",
                name="Short title (max 10 words)",
                description="One-line hook for retrieval (~150 chars)",
                body="Content with **Why:** and **How to apply:**")

4. EDIT: For existing memories that need an update, use read then
   edit with old_string, new_string, and optional near_line.

5. STOP writing once all extractable_candidates have been processed. Return an
   ExtractResult with the list of filenames you wrote and a short summary.

CRITICAL: Do NOT call verify_index or write a session summary. Pass 3 handles
those. Your only output is ExtractResult(filenames, completion_summary).
"""


FINALIZE_SYSTEM_PROMPT = """\
You are the Lerim memory finalization agent. This is Pass 3 of a three-pass
pipeline. You receive the SessionUnderstanding from Pass 1 and the list of
memory filenames written by Pass 2 (both as JSON in your user prompt). Your
job is to fix the index and write the session summary.

HARD RULES — violations waste the request budget and fail the run:
- You do NOT call read("trace") in this pass. Everything you need is already
  in your user prompt (the SessionUnderstanding from Pass 1 plus the list of
  filenames from Pass 2). Reading the trace here is forbidden.
- You call verify_index() at most TWICE: once to check, once to confirm
  after any edit. Not a third time.
- You read() a memory file ONLY when verify_index reports it is broken and
  you must edit() it. Otherwise do not open memory files in this pass.
- You do NOT call scan() in this pass. The filenames from Pass 2 plus
  verify_index() give you all the state you need.

STEPS:

1. VERIFY INDEX: Call verify_index("index.md"). If it returns OK, move
   to step 3. If it returns NOT OK, read the report carefully — it lists
   missing entries, stale entries, broken links, and duplicates.

2. FIX INDEX: Call edit("index.md", old_string, new_string) to add missing
   entries, remove stale ones, and fix broken links. The index format is:
     # Memory Index
     ## User Preferences
     - [Title](filename.md) — one-line description
     ## Project State
     - [Title](filename.md) — one-line description
   Organize by semantic section (User Preferences, Project State, Feedback, etc.).
   After edits, call verify_index again to confirm OK.

3. WRITE SESSION SUMMARY: Call write with type="summary":
     write(
         type="summary",
         name="Short title (max 10 words)",
         description="One-line summary of the session",
         body="## User Intent\\n<one paragraph about what user wanted>\\n\\n## What Happened\\n<one paragraph about what was accomplished and what memories were written>",
     )
   The session summary MUST include both "## User Intent" and "## What Happened"
   headings exactly.

4. RETURN FinalizeResult(completion_summary, index_ok, summary_filename).
   - completion_summary: one-sentence summary of the whole pipeline run
   - index_ok: True if the final verify_index returned OK, else False
   - summary_filename: the filename returned by write for the summary

CRITICAL: Do NOT extract new memories from the trace in this pass. If Pass 2
missed something, that's a Pass 2 bug — don't try to compensate here. Your
only job is: verify_index + edit index + write session summary + return.
"""


# ── UsageLimits per pass ─────────────────────────────────────────────────
#
# Per-pass budgets are NOT defined as module constants — they flow from
# Lerim's config (default.toml / ~/.lerim/config.toml) through
# Config.agent_role.usage_limit_{reflect,extract,finalize} and are passed
# into `run_extraction_three_pass` as required kwargs. Single source of
# truth: the TOML. No hardcoded fallbacks in this module.


# ── Agent builders ───────────────────────────────────────────────────────


def build_reflect_agent(model: OpenAIChatModel) -> Agent[ExtractDeps, SessionUnderstanding]:
	"""Build the Pass 1 reflect agent (read-only tools, SessionUnderstanding output)."""
	return Agent(
		model,
		deps_type=ExtractDeps,
		output_type=SessionUnderstanding,
		system_prompt=REFLECT_SYSTEM_PROMPT,
		tools=[read, grep, scan],
		output_retries=3,
	)


def build_extract_agent(model: OpenAIChatModel) -> Agent[ExtractDeps, ExtractResult]:
	"""Build the Pass 2 extract agent (read + write + edit tools, ExtractResult output)."""
	return Agent(
		model,
		deps_type=ExtractDeps,
		output_type=ExtractResult,
		system_prompt=EXTRACT_SYSTEM_PROMPT,
		tools=[read, grep, scan, write, edit],
		output_retries=3,
	)


def build_finalize_agent(model: OpenAIChatModel) -> Agent[ExtractDeps, FinalizeResult]:
	"""Build the Pass 3 finalize agent (verify_index + edit + write tools, FinalizeResult output)."""
	return Agent(
		model,
		deps_type=ExtractDeps,
		output_type=FinalizeResult,
		system_prompt=FINALIZE_SYSTEM_PROMPT,
		tools=[read, scan, verify_index, edit, write],
		output_retries=3,
	)


# ── Empty-trace short-circuit (no LLM call) ──────────────────────────────


def _python_finalize_empty(deps: ExtractDeps, understanding: SessionUnderstanding) -> FinalizeResult:
	"""Write a minimal "no memories extracted" summary via Python (no LLM call).

	Used by the empty-trace short-circuit after Pass 1 when the
	SessionUnderstanding has no extractable_candidates and no
	existing_memories_relevant. Saves two full agent runs.
	"""
	from datetime import datetime, timezone

	user_goal = (understanding.user_goal or "").strip() or "(no goal identified)"
	summary_body = (
		"## User Intent\n"
		f"{user_goal}\n\n"
		"## What Happened\n"
		"No durable memories were extracted from this session. The trace did not "
		"contain extractable signals per the memory extraction criteria, and no "
		"existing memories needed updating.\n"
	)
	# Include a short timestamp tag so repeated empty sessions don't collide.
	ts = datetime.now(timezone.utc).strftime("%H%M%S")
	# Call the module-level write() tool function directly with a synthetic
	# context — we're Python-side (no LLM), so we bypass the Agent loop.
	ctx = SimpleNamespace(deps=deps)
	result = write(
		ctx,
		type="summary",
		name=f"Empty session {ts}",
		description="No extractable content in session trace.",
		body=summary_body,
	)
	try:
		payload = json.loads(result)
		summary_filename = payload.get("filename", "")
	except json.JSONDecodeError:
		summary_filename = ""

	return FinalizeResult(
		completion_summary="No extractable content.",
		index_ok=True,
		summary_filename=summary_filename,
	)


# ── Pipeline runner ──────────────────────────────────────────────────────


def run_extraction_three_pass(
	memory_root: Path,
	trace_path: Path,
	model: OpenAIChatModel,
	*,
	reflect_limit: int,
	extract_limit: int,
	finalize_limit: int,
	run_folder: Path | None = None,
	return_messages: bool = False,
):
	"""Run the three-pass PydanticAI extraction pipeline.

	Per-pass request budgets are REQUIRED kwargs — the caller must source them
	from Lerim's Config (`Config.agent_role.usage_limit_{reflect,extract,finalize}`)
	or an equivalent config layer. There are no module-level defaults and no
	fallback values in this function. Single source of truth is `default.toml`.

	Args:
		memory_root: Directory containing memory files, index.md, and summaries/.
		trace_path: Path to the session trace .jsonl file.
		model: PydanticAI OpenAIChatModel (built by the canonical builder).
		reflect_limit: Pass 1 request_limit. From `config.agent_role.usage_limit_reflect`.
		extract_limit: Pass 2 request_limit. From `config.agent_role.usage_limit_extract`.
		finalize_limit: Pass 3 request_limit. From `config.agent_role.usage_limit_finalize`.
		run_folder: Optional run workspace folder for artifact output.
		return_messages: If True, return `(FinalizeResult, list[ModelMessage])`
			where the message list is the concatenation of all three passes.
			Default False for backward compatibility.

	Returns:
		FinalizeResult, or a `(FinalizeResult, list)` tuple if return_messages=True.
	"""
	deps = ExtractDeps(
		memory_root=memory_root,
		trace_path=trace_path,
		run_folder=run_folder,
	)

	# Build per-pass UsageLimits from the caller-supplied ints. UsageLimits
	# is an immutable dataclass — constructing once per run is cheap.
	reflect_limits = UsageLimits(request_limit=reflect_limit)
	extract_limits = UsageLimits(request_limit=extract_limit)
	finalize_limits = UsageLimits(request_limit=finalize_limit)

	reflect_agent = build_reflect_agent(model)
	extract_agent = build_extract_agent(model)
	finalize_agent = build_finalize_agent(model)

	# --- Pass 1: Reflect ---
	# Wrap UsageLimitExceeded with a pass label so the eval harness can
	# identify which pass blew its budget. Without this, the error message
	# ("request_limit of N") is ambiguous when multiple passes share a value.
	try:
		reflection = reflect_agent.run_sync(
			"Reflect on the session trace. Read it end-to-end via chunked "
			"read('trace', offset=N, limit=100), scan existing memories, "
			"grep for 'remember', and produce a structured SessionUnderstanding "
			"for the extractor.",
			deps=deps,
			usage_limits=reflect_limits,
		)
	except UsageLimitExceeded as e:
		raise UsageLimitExceeded(
			f"[PASS_1_REFLECT] {e} (budget={reflect_limit})"
		) from e
	understanding: SessionUnderstanding = reflection.output

	# --- Empty-trace short-circuit (saves 2 full agent runs) ---
	if (not understanding.extractable_candidates and
		not understanding.existing_memories_relevant):
		empty_result = _python_finalize_empty(deps, understanding)
		if return_messages:
			return empty_result, list(reflection.all_messages())
		return empty_result

	# --- Pass 2: Extract ---
	try:
		extraction = extract_agent.run_sync(
			"Extract memories per the session understanding below. Apply extraction "
			"criteria and dedup rules. Write novel memories via write() and "
			"edit existing ones via edit() where appropriate.\n\n"
			"SessionUnderstanding:\n"
			f"{understanding.model_dump_json(indent=2)}",
			deps=deps,
			usage_limits=extract_limits,
		)
	except UsageLimitExceeded as e:
		raise UsageLimitExceeded(
			f"[PASS_2_EXTRACT] {e} (budget={extract_limit})"
		) from e
	extracted: ExtractResult = extraction.output

	# --- Pass 3: Finalize ---
	try:
		finalization = finalize_agent.run_sync(
			"Finalize the memory index and write a session summary.\n\n"
			"Session understanding:\n"
			f"{understanding.model_dump_json(indent=2)}\n\n"
			f"Memories written in this session: {extracted.filenames}",
			deps=deps,
			usage_limits=finalize_limits,
		)
	except UsageLimitExceeded as e:
		raise UsageLimitExceeded(
			f"[PASS_3_FINALIZE] {e} (budget={finalize_limit})"
		) from e
	final_result: FinalizeResult = finalization.output

	if return_messages:
		all_messages = (
			list(reflection.all_messages())
			+ list(extraction.all_messages())
			+ list(finalization.all_messages())
		)
		return final_result, all_messages
	return final_result


if __name__ == "__main__":
	"""Self-test: run the three-pass pipeline on a fixture trace and inspect results."""
	import sys
	import tempfile

	from lerim.config.providers import build_pydantic_model

	# Use fixture trace or first CLI arg
	trace_path = Path(sys.argv[1]) if len(sys.argv) > 1 else (
		Path(__file__).parents[3] / "tests" / "fixtures" / "traces" / "claude_short.jsonl"
	)
	if not trace_path.exists():
		print(f"Error: trace not found: {trace_path}")
		sys.exit(1)

	with tempfile.TemporaryDirectory() as tmp:
		memory_root = Path(tmp) / "memory"
		memory_root.mkdir()
		(memory_root / "index.md").write_text("# Memory Index\n")
		(memory_root / "summaries").mkdir()

		print(f"Trace: {trace_path}")
		print(f"Memory root: {memory_root}")
		print()

		model = build_pydantic_model("agent")
		result = run_extraction_three_pass(
			memory_root=memory_root,
			trace_path=trace_path,
			model=model,
		)

		print("=" * 60)
		print("RESULTS")
		print("=" * 60)
		print(f"Completion summary: {result.completion_summary}")
		print(f"Index OK:           {result.index_ok}")
		print(f"Summary filename:   {result.summary_filename}")
		print()

		memories = [f for f in memory_root.glob("*.md") if f.name != "index.md"]
		print(f"Memories written: {len(memories)}")
		for m in memories:
			print(f"  {m.name}")
		print()

		summaries = list((memory_root / "summaries").glob("*.md"))
		print(f"Summaries written: {len(summaries)}")
		for s in summaries:
			print(f"  {s.name}")
		print()

		index = memory_root / "index.md"
		print(f"Index content:\n{index.read_text()}")
