"""OpenAI Agents SDK tools for lerim agent runs.

Only tools that cannot be replaced by Codex:
- write_memory: structured memory creation with lerim-specific validation
- extract_pipeline: calls DSPy extraction (not a filesystem tool)
- summarize_pipeline: calls DSPy summarization (not a filesystem tool)

All filesystem tools (read, write, edit, glob, grep, explore) are handled by codex_tool.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, cast

from agents import RunContextWrapper, function_tool

from lerim.memory.access_tracker import (
	extract_memory_id,
	init_access_db,
	record_access,
)
from lerim.memory.extract_pipeline import extract_memories_from_session_file
from lerim.memory.memory_record import (
	MEMORY_TYPE_FOLDERS,
	MemoryRecord,
	MemoryType,
	canonical_memory_filename,
	slugify,
)
from lerim.memory.summarization_pipeline import (
	summarize_trace_from_session_file,
	write_summary_markdown,
)
from lerim.runtime.oai_context import OAIRuntimeContext

VALID_KINDS = {"insight", "procedure", "friction", "pitfall", "preference"}


def _is_within(path: Path, root: Path) -> bool:
	"""Return whether path is equal to or inside root."""
	resolved = path.resolve()
	root_resolved = root.resolve()
	return resolved == root_resolved or root_resolved in resolved.parents


def _record_memory_access(
	*,
	ctx: OAIRuntimeContext,
	file_path: Path,
) -> None:
	"""Record memory access event for tracking and decay."""
	if not ctx.memory_root:
		return
	if not _is_within(file_path, ctx.memory_root):
		return
	mem_id = extract_memory_id(str(file_path), str(ctx.memory_root))
	if not mem_id:
		return
	init_access_db(ctx.config.memories_db_path)
	record_access(ctx.config.memories_db_path, mem_id, str(ctx.memory_root))


def _write_memory_impl(
	wrapper,
	primitive: str,
	title: str,
	body: str,
	confidence: float = 0.8,
	tags: str = "",
	kind: str = "",
) -> str:
	"""Core write_memory logic — separated for direct unit testing."""
	ctx = wrapper.context

	if not ctx.memory_root:
		return "ERROR: memory_root is not set in runtime context."

	if primitive not in ("decision", "learning"):
		return (
			f"ERROR: Invalid primitive='{primitive}'. "
			"Must be exactly 'decision' or 'learning' (singular, lowercase). "
			"Example: write_memory(primitive='decision', title='...', body='...', confidence=0.8)"
		)

	if not title or not title.strip():
		return (
			"ERROR: title cannot be empty. Provide a short descriptive title. "
			"Example: 'Use SQLite for session indexing'"
		)

	if not (0.0 <= confidence <= 1.0):
		return f"ERROR: confidence={confidence} out of range. Must be 0.0-1.0. Use 0.8 as default."

	effective_kind = kind.strip() or None
	if primitive == "learning" and (not effective_kind or effective_kind not in VALID_KINDS):
		return (
			f"ERROR: Learning memories require 'kind'. Got kind={effective_kind!r}. "
			f"Must be one of: {', '.join(sorted(VALID_KINDS))}. "
			"Example: write_memory(primitive='learning', title='...', body='...', kind='insight')"
		)

	tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []

	try:
		record = MemoryRecord(
			primitive=cast(Literal["decision", "learning"], primitive),
			title=title,
			body=body,
			confidence=confidence,
			tags=tag_list,
			kind=effective_kind,
			id=slugify(title),
			source=ctx.run_id,
		)
	except Exception as exc:
		return (
			f"ERROR: Invalid memory fields: {exc}. "
			"Required: primitive ('decision'|'learning'), title (non-empty string), body (non-empty string). "
			"Optional: confidence (0.0-1.0, default 0.8), tags (comma-separated), kind (required for learnings)."
		)

	mem_type = MemoryType(record.primitive)
	folder = MEMORY_TYPE_FOLDERS[mem_type]
	filename = canonical_memory_filename(title=title, run_id=ctx.run_id)
	target = ctx.memory_root / folder / filename

	content = record.to_markdown()
	target.parent.mkdir(parents=True, exist_ok=True)
	target.write_text(content, encoding="utf-8")
	_record_memory_access(ctx=ctx, file_path=target)

	return json.dumps({
		"file_path": str(target),
		"bytes": len(content.encode("utf-8")),
		"primitive": mem_type.value,
	})


@function_tool
def write_memory(
	wrapper: RunContextWrapper[OAIRuntimeContext],
	primitive: str,
	title: str,
	body: str,
	confidence: float = 0.8,
	tags: str = "",
	kind: str = "",
) -> str:
	"""Write a structured memory record (decision or learning) to the memory folder.

	Args:
		primitive: Memory type, must be 'decision' or 'learning'.
		title: Short descriptive title for the memory.
		body: Memory content in plain language.
		confidence: Confidence score 0.0-1.0. Default 0.8.
		tags: Comma-separated tags. Example: 'queue,reliability'.
		kind: Required for learnings. One of: friction, insight, pitfall, preference, procedure.
	"""
	return _write_memory_impl(wrapper, primitive, title, body, confidence, tags, kind)


@function_tool
def extract_pipeline(
	wrapper: RunContextWrapper[OAIRuntimeContext],
	guidance: str = "",
) -> str:
	"""Run DSPy extraction pipeline on the session trace.

	Args:
		guidance: Optional extraction guidance. If empty, uses default focus on
		          user decisions/preferences while skipping generic research.
	"""
	ctx = wrapper.context

	if not ctx.trace_path or not ctx.artifact_paths:
		return "ERROR: trace_path and artifact_paths required in runtime context."

	output_file = ctx.artifact_paths["extract"]
	effective_guidance = guidance.strip()
	if not effective_guidance:
		effective_guidance = (
			"Focus on user decisions and preferences. "
			"Skip generic research findings, web search results, "
			"and code architecture facts derivable from reading the source."
		)

	candidates = extract_memories_from_session_file(
		ctx.trace_path,
		guidance=effective_guidance,
	)
	output_file.parent.mkdir(parents=True, exist_ok=True)
	output_file.write_text(
		json.dumps(candidates, ensure_ascii=True, indent=2) + "\n", encoding="utf-8"
	)

	return json.dumps({
		"output_path": str(output_file),
		"candidate_count": len(candidates),
	})


@function_tool
def summarize_pipeline(
	wrapper: RunContextWrapper[OAIRuntimeContext],
	guidance: str = "",
) -> str:
	"""Run DSPy summarization pipeline on the session trace.

	Args:
		guidance: Optional summarization guidance for focus areas.
	"""
	ctx = wrapper.context

	if not ctx.memory_root:
		return "ERROR: memory_root required for summarization pipeline."

	if not ctx.trace_path or not ctx.artifact_paths:
		return "ERROR: trace_path and artifact_paths required in runtime context."

	output_file = ctx.artifact_paths["summary"]
	metadata = {
		"run_id": ctx.run_id,
		"trace_path": str(ctx.trace_path),
		"raw_trace_path": str(ctx.trace_path),
	}

	payload = summarize_trace_from_session_file(
		ctx.trace_path,
		metadata=metadata,
		guidance=guidance.strip(),
	)
	summary_path = write_summary_markdown(
		payload, ctx.memory_root, run_id=ctx.run_id
	)

	output_file.parent.mkdir(parents=True, exist_ok=True)
	output_file.write_text(
		json.dumps({"summary_path": str(summary_path)}, ensure_ascii=True, indent=2)
		+ "\n",
		encoding="utf-8",
	)

	return json.dumps({
		"output_path": str(output_file),
		"summary_path": str(summary_path),
	})


if __name__ == "__main__":
	"""Self-test: validate write_memory logic and print tool schemas."""
	import asyncio
	from tempfile import TemporaryDirectory

	from lerim.runtime.oai_context import build_oai_context

	from agents import Agent, Runner, set_tracing_disabled

	set_tracing_disabled(disabled=True)

	with TemporaryDirectory() as tmp_dir:
		root = Path(tmp_dir)
		memory_root = root / "memory"
		(memory_root / "decisions").mkdir(parents=True)
		(memory_root / "learnings").mkdir(parents=True)

		ctx = build_oai_context(
			repo_root=root,
			memory_root=memory_root,
			run_folder=root / "workspace",
			run_id="selftest",
		)

		from agents.extensions.models.litellm_model import LitellmModel
		import os
		from dotenv import load_dotenv
		load_dotenv(Path(__file__).parent.parent.parent.parent / ".env")

		test_model = LitellmModel(
			model="minimax/MiniMax-M2.5",
			api_key=os.environ.get("MINIMAX_API_KEY", ""),
		)

		agent = Agent(
			name="WriteMemoryTest",
			instructions=(
				"You are a test agent. Use the write_memory tool to save a learning with "
				"primitive='learning', title='Queue heartbeat', body='Keep heartbeat updates deterministic.', "
				"confidence=0.8, tags='queue,reliability', kind='insight'. "
				"Then respond with the JSON result from the tool."
			),
			model=test_model,
			tools=[write_memory],
		)

		result = asyncio.run(Runner.run(agent, "Save the memory as instructed.", context=ctx))
		print(f"  Agent response: {result.final_output[:200]}")

		written = list(memory_root.rglob("*.md"))
		assert len(written) > 0, "No memory file written"
		print(f"  Written: {written[0].name}")

	# Print tool schemas
	for tool in [write_memory, extract_pipeline, summarize_pipeline]:
		print(f"  {tool.name}: params={list(tool.params_json_schema.get('properties', {}).keys())}")

	print("oai_tools: self-test passed")
