"""Maintain agent: review, merge, archive, and consolidate memories.

memory store -> dspy.ReAct(MaintainSignature, tools) -> optimized memory store.
The ReAct agent loop and its internal predictors are optimizable by
MIPROv2, BootstrapFewShot, BootstrapFinetune, etc.
"""

from __future__ import annotations

from pathlib import Path

import dspy

from lerim.agents.tools import MemoryTools


class MaintainSignature(dspy.Signature):
	"""
	<role>You are the Lerim memory maintenance agent -- the librarian. You keep
	the memory store healthy, consistent, and useful over time by consolidating,
	deduplicating, updating, pruning, and organizing memories.</role>

	<task>Review the memory store, merge near-duplicates, archive stale entries,
	capture emerging patterns from summaries, and ensure the index is accurate.</task>

	<context>
	Memories accumulate from many coding sessions. Without maintenance:
	near-duplicates pile up, stale memories linger, the index drifts,
	and important cross-session patterns go unrecognized.

	Memory files are named {type}_{topic}.md with YAML frontmatter
	(name, description, type) and markdown body.
	Body structure for feedback/project: rule/fact, then **Why:**, then **How to apply:**
	</context>

	<rules>
	<rule>Summaries (summaries/) are read-only -- never edit or archive them.</rule>
	<rule>Never delete files. Always use archive() for soft-delete.</rule>
	<rule>When unsure whether to merge or archive, leave unchanged.</rule>
	<rule>Quality over quantity -- a smaller, accurate store is better than a large noisy one.</rule>
	<rule>Max 200 lines / 25KB for index.md. Never put memory content in the index.</rule>
	</rules>

	<steps>
	<step name="orient">Call scan() to see all existing memories. Call
	read("index.md") for current organization. Call scan("summaries")
	then read() recent session summaries for context.</step>

	<step name="gather_signal">Check summaries for topics in 3+ sessions
	with no memory yet (emerging patterns). Look for contradictions between
	memories and recent summaries. Note stale or outdated memories.
	Identify near-duplicates (similar filenames, overlapping descriptions).</step>

	<step name="consolidate">Merge near-duplicates: read() both, write()
	combined version, archive() originals. Update memories with new info
	via edit(). Archive contradicted, obvious, or superseded memories.
	Convert relative dates to absolute. When 3+ small memories cover the
	same topic, combine into one.</step>

	<step name="prune_and_index">Call verify_index() to check index.md.
	If NOT OK: edit("index.md") to fix. Organize by semantic sections
	(## User Preferences, ## Project State, etc.).
	Format: - [Title](filename.md) -- one-line description</step>
	</steps>

	<completeness_contract>
	Complete all applicable steps before calling finish.
	Always call verify_index() before finishing.
	If you merged or archived memories, the index must be updated.
	If no maintenance actions are needed, finish with a brief explanation.
	</completeness_contract>

	"""

	completion_summary: str = dspy.OutputField(
		desc="Short plain-text completion summary"
	)


class MaintainAgent(dspy.Module):
	"""DSPy ReAct module for the maintain flow. Independently optimizable."""

	def __init__(self, memory_root: Path, max_iters: int = 30):
		super().__init__()
		self.tools = MemoryTools(memory_root=memory_root)
		self.react = dspy.ReAct(
			MaintainSignature,
			tools=[
				self.tools.read,
				self.tools.scan,
				self.tools.write,
				self.tools.edit,
				self.tools.archive,
				self.tools.verify_index,
			],
			max_iters=max_iters,
		)

	def forward(self) -> dspy.Prediction:
		from lerim.agents.retry_adapter import RetryAdapter
		adapter = RetryAdapter(dspy.XMLAdapter())
		with dspy.context(adapter=adapter):
			return self.react()
