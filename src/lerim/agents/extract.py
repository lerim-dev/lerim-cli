"""PydanticAI ExtractAgent — single-pass with note-taking and dynamic pruning.

The agent reads a coding-agent session trace end-to-end in ONE loop and writes
durable memory files + a session summary. Unlike the previous three-pass
pipeline, this design avoids re-reading the trace across pass boundaries and
instead manages its own context budget via two reasoning-state tools:

- ``note(findings: list[Finding])`` — captures structured findings from
  trace chunks the agent just read. Findings live on ``ctx.deps.notes``
  (mutable state) and as tool-call arguments in conversation history. The
  ``notes_state_injector`` history processor surfaces the running count and
  theme distribution in a compact ``NOTES:`` system message each turn.
- ``prune(trace_offsets: list[int])`` — marks earlier trace-chunk reads for
  stubbing. ``prune_history_processor`` walks the message list before each
  model request and replaces matching ``ToolReturnPart.content`` with
  ``"[pruned]"``. Tool CALLS stay visible (the agent still knows it read
  offset X), only the chunk text is discarded.

A third history processor (``context_pressure_injector``) shows real-time
token-usage pressure so the agent knows when it's safe or necessary to prune.

The per-run request budget is auto-scaled from trace size via
``compute_request_budget(trace_path)``: short traces get 20 turns, 2000-line
traces get 45, pathological inputs clamp at 80.

All six file/search tools (``read``, ``grep``, ``scan``, ``write``, ``edit``,
``verify_index``) are unchanged and live in ``lerim.agents.tools``. This file
owns only:

- ``SYSTEM_PROMPT`` — the combined single-pass prompt
- ``ExtractionResult`` — structured output type
- ``build_extract_agent`` — constructs the PydanticAI ``Agent`` with 8 tools
  and 3 history processors wired in
- ``run_extraction`` — the synchronous runner with auto-scaled request budget

Training readiness: every agent action (``read``, ``note``, ``prune``,
``write``, ``edit``, ``verify_index``) is a discrete tool call that can serve
as an RL action. History processors observe state but never take actions, so
no hidden framework rule competes with the agent for context management.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.models import Model
from pydantic_ai.usage import UsageLimits

from lerim.agents.tools import (
	ExtractDeps,
	compute_request_budget,
	context_pressure_injector,
	edit,
	grep,
	note,
	notes_state_injector,
	prune,
	prune_history_processor,
	read,
	verify_index,
	write,
)


# ── System prompt ────────────────────────────────────────────────────────
#
# Written for an untrained open-weights model (MiniMax-M2.5 class). Dense
# but not verbose: concrete worked example > prose; HARD RULES > aspirational
# guidance. Target length is ≤150% of the pre-redesign single-pass prompt.

SYSTEM_PROMPT = """\
You are the Lerim memory extraction agent. Your goal: read one session
trace, commit to a short summary of what the session was globally about,
write 0-3 memory files (only for durable themes found), and verify the index.
That is the whole job. Be efficient — every extra tool call burns budget.

======================================================================
DASHBOARD (two system messages appear every turn — check them first)
======================================================================

  CONTEXT: <tokens>/<limit> (<pct>%) [soft/hard pressure if >60%/>80%]
  NOTES:   <N> findings (<D> durable, <I> implementation) across <M> theme(s)

CONTEXT tells you how full your context is. NOTES shows your running
synthesis with a durable/implementation split. Only DURABLE findings
(decision, preference, feedback, reference) become memories.
Implementation findings give context but are never written.

======================================================================
CORE FLOW (strict, six steps, no loops)
======================================================================

1. ORIENT (exactly 3 calls):
   - read("index.md")           # see what's already stored
   - verify_index("index.md")   # confirm index matches disk — if NOT OK
                                # the report shows you every real file
                                # on disk; use that as ground truth
   - grep("trace", "remember|memorize|keep in mind|note this")
                                # ONE regex alternation covers 4 synonyms

2. SCAN the trace:
   - read("trace", offset=N, limit=100), incrementing offset by 100.
   - Read every chunk ONCE. Never re-read a chunk you already saw.
   - Every 2-4 chunks, call note() batching ALL findings from those
     chunks into ONE call. Each Finding has four fields:
       Finding(theme, offset, quote, level)
     level is one of: "decision", "preference", "feedback", "reference",
     or "implementation". Classify EACH finding — this is the key filter.
     Example:
       note(findings=[
         Finding(theme="caching", offset=45,  quote="decided to use Redis", level="decision"),
         Finding(theme="caching", offset=178, quote="added Redis client to utils.py", level="implementation"),
       ])
   - If CONTEXT rises above 60%, call prune(trace_offsets=[...]) on
     chunks you have already noted. On small traces (< 5 chunks)
     where CONTEXT stays under 50%, you NEVER need to call prune().

3. SYNTHESIZE (no tool call — think silently):
   Check the NOTES dashboard: how many durable vs implementation findings?
   If 0 durable findings, skip to step 4 (summary only, no memories).
   If durable findings exist, group them into 1-3 themes. Write at the
   THEME level, not one-per-local-candidate. Only durable-level
   findings (decision, preference, feedback, reference) become memories.

4. COMMIT THE SUMMARY FIRST (one write call):
   - write(type="summary", name="...", description="...",
           body="## User Intent\\n<one paragraph>\\n\\n## What Happened\\n<one paragraph>")
     The body MUST come from the NOTES system message + your own
     accumulated note() calls. This summary CRYSTALLIZES what this
     session was about. Every memory you write next must derive from
     one of the themes the summary commits to.
   - Do NOT call read("trace", ...) at this step.
   - ## What Happened is NARRATIVE of the session (what the user did,
     what got decided), NOT a list of filenames.

5. WRITE THEME-LEVEL MEMORIES (one write per theme, zero refinement):
   - For each theme in the summary, write one memory:
     write(type, name, description, body) with **Why:** + **How to apply:**
   - If write() returns "already exists", that's TERMINAL. Do not
     retry this topic. Either read() the existing file and edit() it,
     or skip this candidate. Retrying with a slug variant is a bug.
   - edit() ONLY to update an EXISTING memory discovered in step 1's
     index.md / verify_index report.
   - Do NOT write a memory then edit it then edit it again. Get it
     right the first time.

6. VERIFY the index one last time and return:
   - verify_index("index.md") — should be OK after your writes.
   - If NOT OK, edit("index.md", ...) to add the new memory entries
     (the report tells you exactly what's missing).
   - Return ExtractionResult(completion_summary="wrote N memories + summary").

======================================================================
STRICT DISCIPLINE (violations waste budget and fail the run)
======================================================================

D1. Call grep("trace", ...) exactly ONCE in ORIENT, with a regex
    alternation like "remember|memorize|keep in mind|note this".
    Do NOT grep for other patterns — they rarely pay for themselves.

D2. One write() per final memory. If write() returns "already exists",
    STOP for that topic immediately. The SAME write call with a slug
    variant (e.g. "feedback_foo" → "feedback_foo_v2") is FORBIDDEN.
    Either read+edit the existing file, or skip — do not retry.

D3. Pruning is OPTIONAL on short traces. If CONTEXT never crosses 50%,
    you never need to call prune(). Don't ritually invoke it because
    this prompt mentions it.

D4. Summary is committed FIRST in step 4, then memories derive from it.
    A memory that doesn't fit one of the summary's themes is a noisy
    local extraction — skip it.

D5. Never call read("trace", ...) after step 3. The summary body and
    every memory body must come from the NOTES system message plus
    your own note() tool call history plus the decisions in the
    summary you already committed.

D6. Duplicates are worse than gaps. Skip a candidate when uncertain.
    An empty session (zero memories, only a summary) is valid when
    the trace has no durable signal — pure debugging, ephemeral
    tasks, or content already covered by existing memories.

D7. Explicit remember requests override everything else. If step 1's
    grep returned a user ask like "remember X", you MUST write() or
    edit() a memory for X, even if it looks thin.

======================================================================
WORKED EXAMPLE (compact)
======================================================================

Small trace (e.g. 150 lines, 2 chunks):
  read("index.md") → verify_index() → grep("trace", SYNONYM_REGEX) →
  read(trace,0,100) → read(trace,100,100) → note(findings=[...]) →
  write(summary) → write(project_theme1) → verify_index() → return.
  Total ~9-11 calls. No prune(). No grep repeat.

Large trace (e.g. 2000 lines, 20 chunks):
  Same ORIENT. Then alternate read→note→prune every 3-5 chunks as
  CONTEXT pressure rises. After the last chunk: synthesize silently,
  write summary first, write each theme memory once, verify_index,
  return. Total ~35-50 calls depending on theme count.

No-signal trace (pure bug fix or implementation, ~200 lines):
  Same ORIENT. Read chunks, note observations, but SYNTHESIZE finds
  only implementation details with no durable themes → write summary
  only, skip step 5 → verify_index() → return. Total ~8 calls.

======================================================================
MEMORY FILE FORMAT
======================================================================

Files: {type}_{topic}.md with YAML frontmatter (name, description, type)
and a markdown body.

feedback/project body uses inline bold, NOT ## headings:
  State the rule or decision in ONE line.
  **Why:** reason it matters.
  **How to apply:** concrete action for future sessions.

Example:
  Use tabs for indentation in all code files.
  **Why:** team convention; spaces were rejected in code review.
  **How to apply:** set indent_style=tab; flag spaces in PRs.

Project memories lead with the fact, not a narration:
  Bad:  "## What Happened\\nWe decided to use Redis..."
  Good: "Redis chosen as cache layer (replaced Memcached).\\n**Why:** ..."

Session summary (type="summary") uses two ## headings exactly:
  ## User Intent
  <one paragraph about what the user was trying to do>

  ## What Happened
  <one paragraph narrating what got decided / accomplished — this is
  a narrative of the SESSION, not a list of memory filenames>

======================================================================
EXTRACTION CRITERIA
======================================================================

Extract:
- User role, goals, preferences, working style (about the person)
- Feedback corrections ("don't do X") AND confirmations ("yes, exactly")
- Project decisions, context, constraints NOT in code or git
- Reference pointers to external systems (dashboards, Linear, etc.)

Do NOT extract:
- Code patterns, architecture, file paths, function/module names
- Git history, recent changes (git log is authoritative)
- Debugging solutions (the fix is in the code)
- Anything already in CLAUDE.md or README
- Ephemeral task details, in-progress work
- Generic programming knowledge everyone knows"""


# ── Output type ──────────────────────────────────────────────────────────


class ExtractionResult(BaseModel):
	"""Structured output from the extraction agent."""

	completion_summary: str = Field(
		description="Short plain-text summary of what the run accomplished"
	)


# ── Agent builder ────────────────────────────────────────────────────────


def build_extract_agent(model: Model) -> Agent[ExtractDeps, ExtractionResult]:
	"""Build the single-pass extraction agent with 7 tools + 3 history processors.

	Tools (in wiring order for legibility):
	  read, grep              — read-only file/search
	  note, prune             — reasoning-state management
	  write, edit, verify_index — writes and index maintenance

	Deliberately NOT included: ``scan()``. Smoke testing showed MiniMax
	ignored the prompt rule forbidding scan(); removing it from the tool
	surface is the structural fix. ``read("index.md")`` + ``verify_index()``
	together give the agent the same information scan() would.

	History processors (run in order before each model request):
	  context_pressure_injector — CONTEXT: X/Y (pct%) soft/hard pressure label
	  notes_state_injector      — NOTES: N findings across M themes — ...
	  prune_history_processor   — stubs trace-read results for pruned offsets

	All three processors take ``(ctx: RunContext[ExtractDeps], messages)``
	and read live state from ``ctx.deps`` directly. No closure factories.
	"""
	return Agent(
		model,
		deps_type=ExtractDeps,
		output_type=ExtractionResult,
		system_prompt=SYSTEM_PROMPT,
		tools=[
			read,
			grep,
			note,
			prune,
			write,
			edit,
			verify_index,
		],
		history_processors=[
			context_pressure_injector,
			notes_state_injector,
			prune_history_processor,
		],
		# Tool-call arg validation retries. MiniMax-M2.5's OpenAI-compat
		# layer stochastically emits native XML-format tool calls that
		# fail pydantic schema validation (5-12 failures per run on some
		# traces, especially negative ones). The retry counter is
		# CUMULATIVE per tool name across the whole run, so retries=5
		# was too tight — 6 total schema failures for 'read' would crash
		# even if 20 clean reads succeeded in between. 10 gives enough
		# headroom for the observed flub distribution while still
		# catching genuine infinite-loop bugs.
		retries=10,
		output_retries=3,
	)


# Note: model construction lives in ``lerim.config.providers``. This file
# intentionally does NOT re-export ``build_pydantic_model`` — callers must
# import from the canonical location so the provider/endpoint/fallback
# chain stays readable from one place.


# ── Runner ───────────────────────────────────────────────────────────────


def run_extraction(
	memory_root: Path,
	trace_path: Path,
	model: Model,
	run_folder: Path | None = None,
	return_messages: bool = False,
):
	"""Run the single-pass extraction agent.

	Computes the per-run request budget from trace size via
	``compute_request_budget(trace_path)`` and passes it as
	``UsageLimits(request_limit=budget)`` into ``agent.run_sync``. Short
	traces get ~20 turns, 2000-line traces get ~45, pathological inputs
	clamp at 80.

	Args:
		memory_root: Directory containing memory files, ``index.md``, and
			``summaries/``.
		trace_path: Path to the session trace ``.jsonl`` file.
		model: PydanticAI ``Model`` built by
			``lerim.config.providers.build_pydantic_model`` or
			``build_pydantic_model_from_provider``. Typically a
			``FallbackModel`` wrapping the primary with HTTP retry + provider
			fallback.
		run_folder: Optional run workspace folder for artifact output.
		return_messages: If True, return
			``(ExtractionResult, list[ModelMessage])``. Default False for
			single-return callers.

	Returns:
		``ExtractionResult``, or ``(ExtractionResult, list[ModelMessage])``
		if ``return_messages=True``. The caller can also inspect
		``ExtractDeps`` state after the run by constructing deps externally
		and passing them in (future extension).
	"""
	agent = build_extract_agent(model)
	deps = ExtractDeps(
		memory_root=memory_root,
		trace_path=trace_path,
		run_folder=run_folder,
	)
	usage_limits = UsageLimits(request_limit=compute_request_budget(trace_path))
	result = agent.run_sync(
		"Extract memories from the session trace. Follow the core cycle: "
		"read → note → prune → synthesize → write.",
		deps=deps,
		usage_limits=usage_limits,
	)
	if return_messages:
		return result.output, list(result.all_messages())
	return result.output


# ── Self-test (uv run python -m lerim.agents.extract [trace_path]) ───────


if __name__ == "__main__":
	"""Self-test: run the extraction agent on a fixture trace.

	Prints the final ExtractionResult, the memories written, the summary,
	and the final deps.notes / deps.pruned_offsets so you can eyeball
	whether the agent actually used note() and prune() during the run.
	"""
	import sys
	import tempfile

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
		print(f"Budget: {compute_request_budget(trace_path)} requests")
		print()

		from lerim.config.providers import build_pydantic_model
		model = build_pydantic_model("agent")
		result = run_extraction(
			memory_root=memory_root,
			trace_path=trace_path,
			model=model,
		)

		print("=" * 60)
		print("RESULTS")
		print("=" * 60)
		print(f"Completion summary: {result.completion_summary}")
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
