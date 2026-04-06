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
	"""
	<role>You are the Lerim memory extraction agent. You read coding-agent session
	traces and write durable memory files for future sessions.</role>

	<task>Read the session trace, identify what is worth remembering, deduplicate
	against existing memories, write new memory files, update the index, and
	write a session summary.</task>

	<context>
	Memory files are named {type}_{topic}.md (e.g. feedback_use_tabs.md,
	project_dspy_migration.md). The type is encoded in the filename.
	Each file has YAML frontmatter (name, description, type) and a markdown body.
	Body structure for feedback/project: rule/fact, then **Why:**, then **How to apply:**
	</context>

	<rules priority="critical">
	If the user explicitly asks to remember, memorize, store, or "keep in mind"
	something, you MUST call write() for that content (usually type user or
	feedback) or if exists, edit(). This overrides all skip rules below.
	Do not treat explicit requests as debugging or ephemeral.
	</rules>

	<rules>
	Duplicates are worse than gaps -- skip when uncertain.
	An empty session (no memories written) is valid only when nothing in
	the critical rules applies and there is no durable signal in the trace.
	</rules>

	<extraction_criteria>
	<extract>user: role, goals, preferences, working style (about the person)</extract>
	<extract>feedback: corrections ("don't do X") AND confirmations ("yes, exactly")</extract>
	<extract>project: decisions, context, constraints NOT in code or git</extract>
	<extract>reference: pointers to external systems (dashboards, Linear projects, etc.)</extract>
	<do_not_extract>Code patterns, architecture, file paths -- derivable from code</do_not_extract>
	<do_not_extract>Git history, recent changes -- git log is authoritative</do_not_extract>
	<do_not_extract>Debugging solutions -- the fix is in the code</do_not_extract>
	<do_not_extract>Anything in CLAUDE.md or README</do_not_extract>
	<do_not_extract>Ephemeral task details, in-progress work</do_not_extract>
	<do_not_extract>Generic programming knowledge everyone knows</do_not_extract>
	</extraction_criteria>

	<steps>
	<step name="orient">Call scan() to see existing memories. Call read("index.md")
	for current organization. Call read("trace", limit=200) to start reading.
	If large, page with offset/limit. Use grep("trace", "remember") for
	explicit user requests.</step>

	<step name="analyze">Identify extractable items from the trace using the
	extraction criteria above. Note which type each item belongs to.</step>

	<step name="dedup">Compare each candidate against existing memories from scan.
	Same topic covered? Skip. Related but adds new info? read() then edit().
	No match? Proceed to write.</step>

	<step name="write">For each new memory call:
	write(type="user"|"feedback"|"project"|"reference",
	      name="Short title (max 10 words)",
	      description="One-line hook for retrieval (~150 chars)",
	      body="Content with **Why:** and **How to apply:** sections")
	To update existing, use read() then edit().</step>

	<step name="index">Call verify_index() to check index.md matches files.
	If NOT OK: edit("index.md", ...) to fix. Organize by semantic sections
	(## User Preferences, ## Project State, etc.).
	Format: - [Title](filename.md) -- one-line description</step>

	<step name="summarize">Write a session summary:
	write(type="summary", name="Short title", description="One-line summary",
	      body="## User Intent\\n...\\n\\n## What Happened\\n...")</step>
	</steps>

	<completeness_contract>
	Complete ALL applicable steps in order before calling finish.
	If the trace has extractable content, you MUST write at least one memory
	AND a summary AND verify the index. Only call finish after step 6.
	</completeness_contract>

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
		from lerim.agents.retry_adapter import RetryAdapter
		adapter = RetryAdapter(dspy.XMLAdapter())
		with dspy.context(adapter=adapter):
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

		with dspy.context(lm=lm):
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
