"""DSPy ReAct tools for lerim agent runs.

Each function takes RuntimeContext as its first parameter.
Bind with functools.partial(fn, ctx) before passing to dspy.ReAct.

Tools:
- write_memory: structured memory creation with lerim-specific validation
- extract_pipeline: calls DSPy extraction
- summarize_pipeline: calls DSPy summarization
- write_report: write a JSON report file to the workspace
- read_file: read a file from within allowed directories
- list_files: list files matching a glob pattern
- archive_memory: move a memory file to archived/ subdirectory
- edit_memory: replace a memory file's content
- write_hot_memory: write the hot-memory.md fast-access summary
- memory_search: composite search (scan/keyword/similar/clusters)
- batch_dedup_candidates: batch dedup for all extract candidates
"""

from __future__ import annotations

import inspect
import json
import shutil
from functools import wraps
from pathlib import Path
from typing import Callable, Literal, cast

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
from lerim.runtime.context import RuntimeContext

VALID_KINDS = {"insight", "procedure", "friction", "pitfall", "preference"}
VALID_SOURCE_SPEAKERS = {"user", "agent", "both"}
VALID_DURABILITY = {"permanent", "project", "session"}
VALID_OUTCOMES = {"worked", "failed", "unknown"}


from lerim.runtime.helpers import is_within as _is_within


def _record_memory_access(
	*,
	ctx: RuntimeContext,
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


def write_memory(
	ctx: RuntimeContext,
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


def extract_pipeline(
	ctx: RuntimeContext,
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


def summarize_pipeline(
	ctx: RuntimeContext,
	guidance: str = "",
) -> str:
	"""Summarize the session trace using DSPy and write a markdown summary.

	Use this in step 1 (parallel with extract_pipeline). Produces a structured
	summary and writes it to memory_root/summaries/.

	Returns JSON: {"output_path": str, "summary_path": str}.

	Args:
		guidance: Optional focus areas for the summarization pass.
	"""
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


def write_report(
	ctx: RuntimeContext,
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
	run_folder = ctx.run_folder
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


def read_file(
	ctx: RuntimeContext,
	file_path: str,
) -> str:
	"""Read a file's full text content. Only files under memory_root, run_folder, or extra_read_roots are allowed.

	Use this to inspect memory files, extract artifacts, or summaries in detail.

	Returns the file content as a string, or "Error: ..." on failure.

	Args:
		file_path: Absolute path to the file. Must be under memory_root, run_folder, or extra_read_roots.
	"""
	resolved = Path(file_path).resolve()
	allowed_roots: list[Path] = []
	if ctx.memory_root:
		allowed_roots.append(ctx.memory_root)
	if ctx.run_folder:
		allowed_roots.append(ctx.run_folder)
	for extra in (ctx.extra_read_roots or ()):
		allowed_roots.append(extra)
	if not any(_is_within(resolved, root) for root in allowed_roots):
		return f"Error: path {file_path} is outside allowed roots: {', '.join(str(r) for r in allowed_roots)}"
	if not resolved.exists():
		return f"Error: file not found: {file_path}"
	if not resolved.is_file():
		return f"Error: not a file: {file_path}"
	return resolved.read_text(encoding="utf-8")


def list_files(
	ctx: RuntimeContext,
	directory: str,
	pattern: str = "*.md",
) -> str:
	"""List file paths matching a glob pattern under memory_root, run_folder, or extra_read_roots.

	Use this to discover memory files or artifacts before reading them.

	Returns a JSON array of absolute file paths, or "Error: ..." on failure.

	Args:
		directory: Absolute path to the directory to search in.
		pattern: Glob pattern to filter files. Default "*.md".
	"""
	resolved = Path(directory).resolve()
	allowed_roots: list[Path] = []
	if ctx.memory_root:
		allowed_roots.append(ctx.memory_root)
	if ctx.run_folder:
		allowed_roots.append(ctx.run_folder)
	for extra in (ctx.extra_read_roots or ()):
		allowed_roots.append(extra)
	if not any(_is_within(resolved, root) for root in allowed_roots):
		return f"Error: directory {directory} is outside allowed roots: {', '.join(str(r) for r in allowed_roots)}"
	if not resolved.exists():
		return "[]"
	if not resolved.is_dir():
		return f"Error: not a directory: {directory}"
	files = sorted(str(f) for f in resolved.glob(pattern))
	return json.dumps(files)


def archive_memory(
	ctx: RuntimeContext,
	file_path: str,
) -> str:
	"""Soft-delete a memory by moving it to archived/ (e.g., decisions/foo.md -> archived/decisions/foo.md).

	Use this for low-value, superseded, or duplicate memories. Do NOT delete files directly.

	Returns JSON: {"archived": true, "source": str, "target": str}.

	Args:
		file_path: Absolute path to the memory file under decisions/ or learnings/.
	"""
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


def edit_memory(
	ctx: RuntimeContext,
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


def write_hot_memory(
	ctx: RuntimeContext,
	content: str,
) -> str:
	"""Write the hot-memory.md fast-access summary to memory_root's parent directory.

	Use this in the curate_hot_memory step of maintain. Content should be ~2000 tokens
	with sections: Active Decisions, Key Learnings, Recent Context, Watch Out.

	Returns JSON: {"written": true, "file_path": str, "bytes": int}.

	Args:
		content: Full markdown content for hot-memory.md. No frontmatter required.
	"""
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


def memory_search(
	ctx: RuntimeContext,
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
	from lerim.memory.memory_index import MemoryIndex

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


def batch_dedup_candidates(
	ctx: RuntimeContext,
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
	from lerim.memory.memory_index import MemoryIndex

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


# ---------------------------------------------------------------------------
# Tool binding — produce ctx-bound callables with preserved signatures
# ---------------------------------------------------------------------------

def _bind_tool(fn: Callable, ctx: RuntimeContext) -> Callable:
	"""Bind ctx to a tool function, preserving __name__, __doc__, and signature.

	functools.partial loses the original signature — dspy.Tool sees
	(*args, **kwargs) and name='partial'. This wrapper creates a proper
	function with the ctx parameter removed from the signature so
	dspy.Tool can introspect parameter names, types, and descriptions.
	"""
	sig = inspect.signature(fn)
	params = [p for k, p in sig.parameters.items() if k != "ctx"]
	new_sig = sig.replace(parameters=params)

	@wraps(fn)
	def wrapper(*args, **kwargs):
		return fn(ctx, *args, **kwargs)

	wrapper.__signature__ = new_sig  # type: ignore[attr-defined]
	return wrapper


def bind_sync_tools(ctx: RuntimeContext) -> list[Callable]:
	"""Build the tool list for the sync flow, bound to ctx."""
	return [
		_bind_tool(extract_pipeline, ctx),
		_bind_tool(summarize_pipeline, ctx),
		_bind_tool(write_memory, ctx),
		_bind_tool(write_report, ctx),
		_bind_tool(read_file, ctx),
		_bind_tool(list_files, ctx),
		_bind_tool(batch_dedup_candidates, ctx),
	]


def bind_maintain_tools(ctx: RuntimeContext) -> list[Callable]:
	"""Build the tool list for the maintain flow, bound to ctx."""
	return [
		_bind_tool(write_memory, ctx),
		_bind_tool(write_report, ctx),
		_bind_tool(read_file, ctx),
		_bind_tool(list_files, ctx),
		_bind_tool(archive_memory, ctx),
		_bind_tool(edit_memory, ctx),
		_bind_tool(write_hot_memory, ctx),
		_bind_tool(memory_search, ctx),
	]


def bind_ask_tools(ctx: RuntimeContext) -> list[Callable]:
	"""Build the tool list for the ask flow, bound to ctx."""
	return [
		_bind_tool(memory_search, ctx),
		_bind_tool(read_file, ctx),
		_bind_tool(list_files, ctx),
	]
