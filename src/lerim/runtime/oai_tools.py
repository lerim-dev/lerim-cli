"""OpenAI Agents SDK tools for lerim agent runs.

Tools:
- write_memory: structured memory creation with lerim-specific validation
- extract_pipeline: calls DSPy extraction (not a filesystem tool)
- summarize_pipeline: calls DSPy summarization (not a filesystem tool)
- write_report: write a JSON report file to the workspace
- read_file: read a file from within allowed directories
- archive_memory: move a memory file to archived/ subdirectory
- edit_memory: replace a memory file's content (frontmatter + body)
- write_hot_memory: write the hot-memory.md file at memory_root parent
- memory_search: composite search tool (scan/keyword/similar/clusters in one call)
- batch_dedup_candidates: batch dedup for all extract candidates in a single call

Simple reads/writes use the lightweight tools above.
"""

from __future__ import annotations

import json
import shutil
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
VALID_SOURCE_SPEAKERS = {"user", "agent", "both"}
VALID_DURABILITY = {"permanent", "project", "session"}
VALID_OUTCOMES = {"worked", "failed", "unknown"}


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
	source_speaker: str = "both",
	durability: str = "project",
	outcome: str = "",
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

	effective_source_speaker = source_speaker.strip() or "both"
	if effective_source_speaker not in VALID_SOURCE_SPEAKERS:
		return (
			f"ERROR: source_speaker={effective_source_speaker!r} is invalid. "
			f"Must be one of: {', '.join(sorted(VALID_SOURCE_SPEAKERS))}."
		)

	effective_durability = durability.strip() or "project"
	if effective_durability not in VALID_DURABILITY:
		return (
			f"ERROR: durability={effective_durability!r} is invalid. "
			f"Must be one of: {', '.join(sorted(VALID_DURABILITY))}."
		)

	effective_outcome = outcome.strip() or None
	if effective_outcome is not None and effective_outcome not in VALID_OUTCOMES:
		return (
			f"ERROR: outcome={effective_outcome!r} is invalid. "
			f"Must be one of: {', '.join(sorted(VALID_OUTCOMES))}."
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
			source_speaker=cast(Literal["user", "agent", "both"], effective_source_speaker),
			durability=cast(Literal["permanent", "project", "session"], effective_durability),
			outcome=cast(Literal["worked", "failed", "unknown"] | None, effective_outcome),
			id=slugify(title),
			source=ctx.run_id,
		)
	except Exception as exc:
		return (
			f"ERROR: Invalid memory fields: {exc}. "
			"Required: primitive ('decision'|'learning'), title (non-empty string), body (non-empty string). "
			"Optional: confidence (0.0-1.0, default 0.8), tags (comma-separated), "
			"kind (required for learnings), source_speaker, durability, outcome."
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
	source_speaker: str = "both",
	durability: str = "project",
	outcome: str = "",
) -> str:
	"""Create a memory file (decision or learning) under memory_root.

	Use this as the ONLY way to persist new memories. Call once per candidate
	classified as "add" or "update" in the classify step.

	Returns JSON: {"file_path": str, "bytes": int, "primitive": str}.

	Args:
		primitive: "decision" or "learning" (singular, lowercase). No other values accepted.
		title: Short descriptive title. Used to generate the filename slug.
		body: Memory content in plain text or markdown.
		confidence: Float 0.0-1.0. Default 0.8. Higher = more certain.
		tags: Comma-separated tags. Example: "queue,reliability".
		kind: Required for learnings. One of: "friction", "insight", "pitfall", "preference", "procedure".
		source_speaker: Who originated the memory: "user", "agent", or "both".
		durability: Expected lifespan: "permanent", "project", or "session".
		outcome: Optional validation status: "worked", "failed", or "unknown".
	"""
	return _write_memory_impl(
		wrapper,
		primitive,
		title,
		body,
		confidence,
		tags,
		kind,
		source_speaker,
		durability,
		outcome,
	)


@function_tool
def extract_pipeline(
	wrapper: RunContextWrapper[OAIRuntimeContext],
	guidance: str = "",
) -> str:
	"""Extract memory candidates from the session trace using DSPy.

	Use this in step 1 (parallel with summarize_pipeline). Reads the trace,
	runs chunked extraction, and writes candidates to the extract artifact.

	Returns JSON: {"output_path": str, "candidate_count": int}.

	Args:
		guidance: Focus instructions for extraction. Default: extract user
		          decisions/preferences, skip generic web search noise.
	"""
	ctx = wrapper.context

	if not ctx.trace_path or not ctx.artifact_paths:
		return "ERROR: trace_path and artifact_paths required in runtime context."

	output_file = ctx.artifact_paths["extract"]
	effective_guidance = guidance.strip()
	if not effective_guidance:
		effective_guidance = (
			"Focus on user decisions, preferences, and strategic conclusions. "
			"Extract research findings the user explicitly requested that led to decisions or direction changes. "
			"Skip generic web search noise and code architecture facts derivable from reading the source."
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
	"""Summarize the session trace using DSPy and write a markdown summary.

	Use this in step 1 (parallel with extract_pipeline). Produces a structured
	summary and writes it to memory_root/summaries/.

	Returns JSON: {"output_path": str, "summary_path": str}.

	Args:
		guidance: Optional focus areas for the summarization pass.
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


@function_tool
def write_report(
	ctx: RunContextWrapper[OAIRuntimeContext],
	file_path: str,
	content: str,
) -> str:
	"""Write a JSON report file to the run workspace folder.

	Use this as the final step to persist the run report. Content MUST be
	valid JSON. Path MUST be inside the run workspace.

	Returns a confirmation string on success or "Error: ..." on failure.

	Args:
		file_path: Absolute path within run_folder to write to.
		content: Valid JSON string. Invalid JSON is rejected.
	"""
	resolved = Path(file_path).resolve()
	run_folder = ctx.context.run_folder
	if not run_folder:
		return "Error: run_folder is not set in runtime context"
	if not _is_within(resolved, run_folder):
		return f"Error: path {file_path} is outside the workspace {run_folder}"
	try:
		json.loads(content)
	except json.JSONDecodeError:
		return "Error: content is not valid JSON"
	resolved.parent.mkdir(parents=True, exist_ok=True)
	resolved.write_text(content, encoding="utf-8")
	return f"Report written to {file_path}"


@function_tool
def read_file(
	ctx: RunContextWrapper[OAIRuntimeContext],
	file_path: str,
) -> str:
	"""Read a file's full text content. Only files under memory_root or run_folder are allowed.

	Use this to inspect memory files, extract artifacts, or summaries in detail.

	Returns the file content as a string, or "Error: ..." on failure.

	Args:
		file_path: Absolute path to the file. Must be under memory_root or run_folder.
	"""
	resolved = Path(file_path).resolve()
	run_folder = ctx.context.run_folder
	memory_root = ctx.context.memory_root
	allowed = False
	if run_folder and _is_within(resolved, run_folder):
		allowed = True
	if memory_root and _is_within(resolved, memory_root):
		allowed = True
	if not allowed:
		roots = []
		if memory_root:
			roots.append(str(memory_root))
		if run_folder:
			roots.append(str(run_folder))
		return f"Error: path {file_path} is outside allowed roots: {', '.join(roots)}"
	if not resolved.exists():
		return f"Error: file not found: {file_path}"
	if not resolved.is_file():
		return f"Error: not a file: {file_path}"
	return resolved.read_text(encoding="utf-8")


@function_tool
def list_files(
	ctx: RunContextWrapper[OAIRuntimeContext],
	directory: str,
	pattern: str = "*.md",
) -> str:
	"""List file paths matching a glob pattern under memory_root or run_folder.

	Use this to discover memory files or artifacts before reading them.

	Returns a JSON array of absolute file paths, or "Error: ..." on failure.

	Args:
		directory: Absolute path to the directory to search in.
		pattern: Glob pattern to filter files. Default "*.md".
	"""
	resolved = Path(directory).resolve()
	run_folder = ctx.context.run_folder
	memory_root = ctx.context.memory_root
	allowed = False
	if run_folder and _is_within(resolved, run_folder):
		allowed = True
	if memory_root and _is_within(resolved, memory_root):
		allowed = True
	if not allowed:
		roots = []
		if memory_root:
			roots.append(str(memory_root))
		if run_folder:
			roots.append(str(run_folder))
		return f"Error: directory {directory} is outside allowed roots: {', '.join(roots)}"
	if not resolved.exists():
		return "[]"
	if not resolved.is_dir():
		return f"Error: not a directory: {directory}"
	files = sorted(str(f) for f in resolved.glob(pattern))
	return json.dumps(files)


def _archive_memory_impl(
	wrapper,
	file_path: str,
) -> str:
	"""Core archive_memory logic — separated for direct unit testing."""
	ctx = wrapper.context

	if not ctx.memory_root:
		return "ERROR: memory_root is not set in runtime context."

	resolved = Path(file_path).resolve()
	if not _is_within(resolved, ctx.memory_root):
		return f"ERROR: path {file_path} is outside memory_root {ctx.memory_root}"

	if not resolved.exists():
		return f"ERROR: file not found: {file_path}"

	if not resolved.is_file():
		return f"ERROR: not a file: {file_path}"

	# Determine the subfolder (decisions or learnings)
	try:
		rel = resolved.relative_to(ctx.memory_root)
	except ValueError:
		return f"ERROR: path {file_path} is not relative to memory_root {ctx.memory_root}"

	parts = rel.parts
	if len(parts) < 2 or parts[0] not in ("decisions", "learnings"):
		return (
			f"ERROR: path must be under decisions/ or learnings/ within memory_root. "
			f"Got: {rel}"
		)

	# Build archived target: memory_root/archived/{subfolder}/{filename}
	target = ctx.memory_root / "archived" / rel
	target.parent.mkdir(parents=True, exist_ok=True)
	shutil.move(str(resolved), str(target))

	return json.dumps({
		"archived": True,
		"source": str(resolved),
		"target": str(target),
	})


@function_tool
def archive_memory(
	ctx: RunContextWrapper[OAIRuntimeContext],
	file_path: str,
) -> str:
	"""Soft-delete a memory by moving it to archived/ (e.g., decisions/foo.md -> archived/decisions/foo.md).

	Use this for low-value, superseded, or duplicate memories. Do NOT delete files directly.

	Returns JSON: {"archived": true, "source": str, "target": str}.

	Args:
		file_path: Absolute path to the memory file under decisions/ or learnings/.
	"""
	return _archive_memory_impl(ctx, file_path)


def _edit_memory_impl(
	wrapper,
	file_path: str,
	new_content: str,
) -> str:
	"""Core edit_memory logic — separated for direct unit testing."""
	ctx = wrapper.context

	if not ctx.memory_root:
		return "ERROR: memory_root is not set in runtime context."

	resolved = Path(file_path).resolve()
	if not _is_within(resolved, ctx.memory_root):
		return f"ERROR: path {file_path} is outside memory_root {ctx.memory_root}"

	if not resolved.exists():
		return f"ERROR: file not found: {file_path}"

	if not resolved.is_file():
		return f"ERROR: not a file: {file_path}"

	# Validate new_content has YAML frontmatter
	stripped = new_content.strip()
	if not stripped.startswith("---"):
		return "ERROR: new_content must start with YAML frontmatter (---)"

	resolved.write_text(new_content, encoding="utf-8")
	_record_memory_access(ctx=ctx, file_path=resolved)

	return json.dumps({
		"edited": True,
		"file_path": str(resolved),
		"bytes": len(new_content.encode("utf-8")),
	})


@function_tool
def edit_memory(
	ctx: RunContextWrapper[OAIRuntimeContext],
	file_path: str,
	new_content: str,
) -> str:
	"""Replace the full content of an existing memory file (frontmatter + body).

	Use this to merge content from duplicates, update confidence, or add tags.
	The file MUST already exist under memory_root.

	Returns JSON: {"edited": true, "file_path": str, "bytes": int}.

	Args:
		file_path: Absolute path to the memory file to overwrite.
		new_content: Complete replacement content. MUST start with "---" (YAML frontmatter).
	"""
	return _edit_memory_impl(ctx, file_path, new_content)


def _write_hot_memory_impl(
	wrapper,
	content: str,
) -> str:
	"""Core write_hot_memory logic — separated for direct unit testing."""
	ctx = wrapper.context

	if not ctx.memory_root:
		return "ERROR: memory_root is not set in runtime context."

	hot_path = ctx.memory_root.parent / "hot-memory.md"
	hot_path.parent.mkdir(parents=True, exist_ok=True)
	hot_path.write_text(content, encoding="utf-8")

	return json.dumps({
		"written": True,
		"file_path": str(hot_path),
		"bytes": len(content.encode("utf-8")),
	})


@function_tool
def write_hot_memory(
	ctx: RunContextWrapper[OAIRuntimeContext],
	content: str,
) -> str:
	"""Write the hot-memory.md fast-access summary to memory_root's parent directory.

	Use this in the curate_hot_memory step of maintain. Content should be ~2000 tokens
	with sections: Active Decisions, Key Learnings, Recent Context, Watch Out.

	Returns JSON: {"written": true, "file_path": str, "bytes": int}.

	Args:
		content: Full markdown content for hot-memory.md. No frontmatter required.
	"""
	return _write_hot_memory_impl(ctx, content)


def _memory_search_impl(
	wrapper,
	query: str,
	mode: str = "similar",
	title: str = "",
	body: str = "",
	limit: int = 5,
	primitive: str = "",
	tags: str = "",
	min_group_size: int = 3,
) -> str:
	"""Core memory_search logic -- separated for direct unit testing."""
	from lerim.memory.memory_index import MemoryIndex

	ctx = wrapper.context
	memory_root = ctx.memory_root
	if not memory_root:
		return "Error: no memory_root configured"
	db_path = ctx.config.memories_db_path
	index = MemoryIndex(db_path)
	index.ensure_schema()

	# Always reindex to catch new/changed files regardless of mode
	reindex_stats = index.reindex_directory(memory_root)

	if mode == "scan":
		results = index.scan_all(primitive=primitive or None)
		return json.dumps({"mode": "scan", "count": len(results), "reindex": reindex_stats, "memories": results}, default=str)

	if mode == "keyword":
		results = index.search(query, limit=limit, primitive=primitive or None)
		return json.dumps({"mode": "keyword", "query": query, "count": len(results), "results": results}, default=str)

	if mode == "similar":
		results = index.find_similar(title or query, body, tags=tags, limit=limit)
		return json.dumps({"mode": "similar", "count": len(results), "results": results}, default=str)

	if mode == "clusters":
		clusters = index.find_clusters(min_cluster_size=min_group_size)
		formatted = [{"size": len(c), "memories": c} for c in clusters]
		return json.dumps({"mode": "clusters", "cluster_count": len(formatted), "clusters": formatted}, default=str)

	return f"Error: unknown mode '{mode}'. Use: similar, keyword, scan, clusters"


@function_tool
def memory_search(
	ctx: RunContextWrapper[OAIRuntimeContext],
	query: str,
	mode: str = "similar",
	title: str = "",
	body: str = "",
	limit: int = 5,
	primitive: str = "",
	tags: str = "",
	min_group_size: int = 3,
) -> str:
	"""Search existing memories by keyword, similarity, or tag clusters.

	Reindexes on every call to capture new/changed files.

	mode="scan": Return compact metadata catalog (id, title, tags, confidence, kind, file_path).
	  Use primitive= to filter by "decision" or "learning".
	mode="keyword": BM25-ranked keyword search. Returns {mode, query, count, results}.
	mode="similar": Find memories similar to a candidate for dedup. Pass title + body + tags.
	  Returns {mode, count, results: [{title, similarity, lexical_similarity, fused_score, ...}]}.
	mode="clusters": Find groups of related memories sharing tags for merge review.
	  Returns {mode, cluster_count, clusters: [{size, memories}]}.

	Args:
		query: Search text. Required for keyword/similar. Use "" for scan/clusters.
		mode: One of "scan", "keyword", "similar", "clusters". Default "similar".
		title: Candidate title (similar mode). Ignored by other modes.
		body: Candidate body (similar mode). Ignored by other modes.
		limit: Max results. Default 5. Applies to keyword/similar modes.
		primitive: Filter by "decision" or "learning". Applies to scan/keyword modes.
		tags: Comma-separated tags for better matching in similar mode.
		min_group_size: Minimum cluster size for clusters mode. Default 3.
	"""
	return _memory_search_impl(ctx, query, mode, title, body, limit, primitive, tags, min_group_size)


def _batch_dedup_candidates_impl(
	wrapper,
	candidates_json: str,
) -> str:
	"""Core batch_dedup_candidates logic -- separated for direct unit testing."""
	from lerim.memory.memory_index import MemoryIndex

	ctx = wrapper.context
	memory_root = ctx.memory_root
	if not memory_root:
		return "Error: no memory_root configured"

	try:
		candidates = json.loads(candidates_json)
	except json.JSONDecodeError as exc:
		return f"Error: invalid JSON input: {exc}"

	if isinstance(candidates, dict):
		candidates = candidates.get("candidates", candidates.get("memories", []))

	if not isinstance(candidates, list):
		return "Error: expected a JSON array or object with 'candidates'/'memories' key"

	db_path = ctx.config.memories_db_path
	index = MemoryIndex(db_path)
	index.ensure_schema()
	index.reindex_directory(memory_root)

	enriched = []
	for c in candidates:
		if not isinstance(c, dict):
			continue
		c_title = c.get("title", "")
		c_body = c.get("body", "")
		c_tags = ",".join(c.get("tags", [])) if isinstance(c.get("tags"), list) else str(c.get("tags", ""))
		similar = index.find_similar(c_title, c_body, tags=c_tags, limit=3)
		top_similarity = 0.0
		if similar:
			top = similar[0]
			top_similarity = max(
				float(top.get("similarity") or 0.0),
				float(top.get("lexical_similarity") or 0.0),
			)
		enriched.append({
			"candidate": c,
			"similar_existing": similar,
			"top_similarity": top_similarity,
		})

	return json.dumps({"count": len(enriched), "results": enriched}, default=str)


@function_tool
def batch_dedup_candidates(
	ctx: RunContextWrapper[OAIRuntimeContext],
	candidates_json: str,
) -> str:
	"""Find similar existing memories for ALL extract candidates in one call.

	Use this in step 2 (after extract_pipeline). Pass the raw extract.json
	content. Each candidate is enriched with its top-3 similar existing
	memories and a top_similarity score for dedup classification.

	Interpreting top_similarity scores:
	- top_similarity uses normalized 0.0-1.0 similarity (prefer semantic similarity,
	  fall back to lexical overlap when vector data is unavailable).
	- 0.65+ : Very likely duplicate. Classify as "no_op" unless candidate has
	  clearly distinct information not present in the existing memory.
	- 0.40-0.65 : Related topic. Read both carefully. Classify as "update" if
	  candidate adds genuinely new facts, "no_op" if it's just rephrasing.
	- Below 0.40 : Likely a new topic. Classify as "add".
	- 0.0 : No existing memories at all (empty store). All candidates are "add".

	Returns JSON: {"count": int, "results": [{"candidate": {...},
	  "similar_existing": [...], "top_similarity": float}]}.

	Args:
		candidates_json: The full JSON content of the extract artifact file.
	"""
	return _batch_dedup_candidates_impl(ctx, candidates_json)


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
	for tool in [write_memory, extract_pipeline, summarize_pipeline, write_report, read_file, list_files, archive_memory, edit_memory, write_hot_memory, memory_search, batch_dedup_candidates]:
		print(f"  {tool.name}: params={list(tool.params_json_schema.get('properties', {}).keys())}")

	print("oai_tools: self-test passed")
