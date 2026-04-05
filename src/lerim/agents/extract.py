"""Extract agent: extract memories from a session trace, dedup, and write.

session trace -> dspy.ReAct(ExtractSignature, tools) -> memory files + summary.
The ReAct agent loop and its internal predictors are optimizable by
MIPROv2, BootstrapFewShot, BootstrapFinetune, etc.
"""

from __future__ import annotations

from pathlib import Path

import dspy

from lerim.agents.tools import MemoryTools


class ExtractSignature(dspy.Signature):
	"""Extract durable memories from a coding-agent session trace.

	You are the Lerim memory extraction agent. Read the session trace, identify what's
	worth remembering for future sessions, and write memory files directly.

	Memory files are named {type}_{topic}.md (e.g. feedback_use_tabs.md,
	project_dspy_migration.md). The type is encoded in the filename.
	Each file has YAML frontmatter (name, description, type) and a markdown body.

	PRIORITY (overrides default skip and most DO NOT EXTRACT below):
	If the user explicitly asks to remember, memorize, store, or "keep in mind"
	something, you MUST call write() for that content (usually type user or
	feedback) or if exists, edit(). Do not treat that as debugging or ephemeral.
	Do not skip because "uncertain" when the request to remember is clear.

	Steps:

	1. ORIENT:
	   Call scan() to see existing memories (returns filename, description,
	   modified time for each). Filenames tell you the type and topic.
	   Call read("index.md") to see the current index organization.
	   Call read("trace", limit=200) to start reading the session trace.
	   If the trace is large, page through with offset/limit.
	   Use grep("trace", "remember") to find explicit user requests.

	2. ANALYZE:
	   From the trace, identify items worth remembering. Apply these criteria:

	   EXTRACT (high-value only, except PRIORITY above always wins):
	   - user: role, goals, preferences, working style (about the person)
	   - feedback: corrections ("don't do X") AND confirmations ("yes, exactly")
	     Body: rule/fact -> **Why:** -> **How to apply:**
	   - project: decisions, context, constraints NOT in code or git
	     Body: fact/decision -> **Why:** -> **How to apply:**
	   - reference: pointers to external systems (dashboards, Linear projects, etc.)

	   DO NOT EXTRACT (does not apply to PRIORITY requests to remember):
	   - Code patterns, architecture, file paths -- derivable by reading the code
	   - Git history, recent changes -- git log is authoritative
	   - Debugging solutions -- the fix is in the code
	   - Anything in CLAUDE.md or README
	   - Ephemeral task details, in-progress work
	   - Generic programming knowledge everyone knows
	   - Implementation details visible in the codebase

	   An empty session (no memories written) is valid only when nothing in PRIORITY
	   applies and the session is pure implementation with no durable signal.

	3. DEDUP:
	   Compare each potential memory against the manifest from step 1.
	   - Existing memory covers same topic (check filename and description) -> skip
	   - Related but adds NEW info -> read() the existing file, then edit() to update
	   - No match -> write() to create
	   Default to skipping when uncertain -- duplicates are worse than gaps, unless
	   PRIORITY (explicit remember/memorize) applies; then write.

	4. WRITE:
	   For each new memory:
	   write(type="user"|"feedback"|"project"|"reference",
	         name="Short title (max 10 words)",
	         description="One-line hook for retrieval (~150 chars)",
	         body="Content: rule/fact, then **Why:**, then **How to apply:**")

	   To update an existing memory, use read() then edit() with the changes.

	5. INDEX:
	   Call verify_index() to check if index.md matches actual files.
	   If NOT OK: use edit("index.md", ...) to add missing entries and
	   remove stale ones.
	   If OK or after fixing: call read("index.md") for a final check.
	   Verify format, section organization, and descriptions are clear.
	   Organize entries semantically by section (## User Preferences,
	   ## Project State, etc.), not flat.
	   Format: - [Title](filename.md) -- one-line description

	6. SUMMARIZE:
	   Write a session summary:
	   write(type="summary",
	         name="Short session title (max 10 words)",
	         description="One-line description of what was achieved",
	         body="## User Intent\\n<goal, max 150 words>\\n\\n## What Happened\\n<narrative, max 200 words>")

	Return a short completion line.
	"""

	completion_summary: str = dspy.OutputField(
		desc="Short plain-text completion summary"
	)


class ExtractAgent(dspy.Module):
	"""DSPy ReAct module for the extract flow. Independently optimizable."""

	def __init__(self, memory_root: Path, trace_path: Path,
	             run_folder: Path | None = None, max_iters: int = 15):
		super().__init__()
		self.tools = MemoryTools(
			memory_root=memory_root,
			trace_path=trace_path,
			run_folder=run_folder,
		)
		self.react = dspy.ReAct(
			ExtractSignature,
			tools=[
				self.tools.read,
				self.tools.grep,
				self.tools.scan,
				self.tools.write,
				self.tools.edit,
				self.tools.verify_index,
			],
			max_iters=max_iters,
		)

	def forward(self) -> dspy.Prediction:
		return self.react()


if __name__ == "__main__":
	"""Self-test: run ExtractAgent on a fixture trace and inspect results."""
	import sys

	from lerim.config.settings import get_config
	from lerim.config.providers import build_dspy_lm

	config = get_config()
	lm = build_dspy_lm("agent", config=config)

	# Use fixture trace or first CLI arg
	trace_path = Path(sys.argv[1]) if len(sys.argv) > 1 else (
		Path(__file__).parents[3] / "tests" / "fixtures" / "traces" / "claude_short.jsonl"
	)
	if not trace_path.exists():
		print(f"Error: trace not found: {trace_path}")
		sys.exit(1)

	# Temp memory dir
	import tempfile
	with tempfile.TemporaryDirectory() as tmp:
		memory_root = Path(tmp) / "memory"
		memory_root.mkdir()
		(memory_root / "index.md").write_text("# Memory Index\n")
		(memory_root / "summaries").mkdir()

		print(f"Trace: {trace_path}")
		print(f"Memory root: {memory_root}")
		print(f"LM: {config.agent_role.provider}/{config.agent_role.model}")
		print(f"Max iters: {config.agent_role.max_iters_sync}")
		print()

		agent = ExtractAgent(
			memory_root=memory_root,
			trace_path=trace_path,
			max_iters=config.agent_role.max_iters_sync,
		)

		with dspy.context(lm=lm, adapter=dspy.XMLAdapter()):
			prediction = agent()

		# Results
		print("=" * 60)
		print("RESULTS")
		print("=" * 60)
		print(f"Summary: {prediction.completion_summary}")
		print()

		# Memories written
		memories = [f for f in memory_root.glob("*.md") if f.name != "index.md"]
		print(f"Memories written: {len(memories)}")
		for m in memories:
			print(f"  {m.name}")
		print()

		# Summaries
		summaries = list((memory_root / "summaries").glob("*.md"))
		print(f"Summaries written: {len(summaries)}")
		for s in summaries:
			print(f"  {s.name}")
		print()

		# Index
		index = memory_root / "index.md"
		print(f"Index content:\n{index.read_text()}")

		# Trajectory
		trajectory = getattr(prediction, "trajectory", {}) or {}
		print(f"\nTrajectory ({len(trajectory)} entries):")
		for key in sorted(trajectory.keys()):
			val = str(trajectory[key])[:200]
			print(f"  {key}: {val}")

		# LM history (last few calls)
		history = getattr(lm, "history", []) or []
		print(f"\nLM calls: {len(history)}")
		if history:
			last = history[-1]
			print(f"  Last call response (truncated): {str(last.get('response', ''))[:300]}")
