"""PydanticAI tool functions for Lerim memory agents.

Standalone module-level functions that agents register via
`Agent(tools=[read, grep, scan, write, edit, verify_index])`. Each tool
takes `RunContext[ExtractDeps]` as its first argument; docstrings follow
Google style (Args: section) and are parsed by PydanticAI via griffe into
the JSON schema the model sees as tool metadata.

This module is the **single source of truth** for memory operations. The
`MemoryTools` class at the bottom is a thin compatibility adapter kept
only so the legacy DSPy maintain/ask agents can continue binding methods
to `dspy.ReAct(tools=[...])` without modification. New code should use
the module-level functions directly.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import frontmatter as fm_lib
import yaml
from pydantic import BaseModel, Field
from pydantic_ai import RunContext
from pydantic_ai.messages import (
	ModelMessage,
	ModelRequest,
	ModelResponse,
	SystemPromptPart,
	ToolCallPart,
	ToolReturnPart,
)

MEMORY_TYPES = ("user", "feedback", "project", "reference", "summary")

# Model context window cap (tokens). Used by context_pressure_injector to
# compute graduated soft/hard pressure thresholds. 128K is MiniMax-M2.5's
# limit and a safe baseline for Claude/GPT-4 class models too. If a future
# provider has a smaller window, override via config (future work).
MODEL_CONTEXT_TOKEN_LIMIT = 128_000
CONTEXT_SOFT_PRESSURE_PCT = 0.60
CONTEXT_HARD_PRESSURE_PCT = 0.80

# Rough token estimator constant: ~4 chars per token for English+code.
# Not exact (real tokenizers vary), but stable enough for soft/hard
# thresholding. Underestimating is worse than overestimating here, so
# we round UP in the injector.
_TOKENS_PER_CHAR = 0.25

# Hard caps on read("trace", ...) output. The line-count cap forces
# chunked reads (agent paginates via offset) so trajectories stay bounded.
# The byte caps protect the model's context window from single huge trace
# lines (e.g. massive tool results) and runaway chunk payloads that would
# otherwise blow the input limit. Memory-file reads are unbounded — memory
# files are small by design.
TRACE_MAX_LINES_PER_READ = 100
TRACE_MAX_LINE_BYTES = 5_000       # per-line truncation cap
TRACE_MAX_CHUNK_BYTES = 50_000     # total chunk payload cap


# ── Reasoning schemas ────────────────────────────────────────────────────


class Finding(BaseModel):
	"""A structured finding captured by the agent during trace scanning.

	Findings are the atom of the agent's persistent reasoning state. One
	note() tool call can record a list of Findings; they accumulate in
	ctx.deps.notes and stay visible to the agent both via the NOTES system
	message (injected by notes_state_injector each turn) and via the note()
	tool call arguments that persist in conversation history.

	Each Finding must point back to the trace via `offset` so the agent can
	re-read the exact chunk if needed when writing the final memory. This
	preserves evidence fidelity even after the originating chunk read has
	been stubbed by prune().
	"""

	theme: str = Field(
		description="Short theme label, e.g. 'DSPy migration' or 'config refactor'"
	)
	offset: int = Field(description="Trace line where the supporting evidence appears")
	quote: str = Field(
		description=(
			"Verbatim quote from the trace supporting this finding. Keep it "
			"short — under ~200 characters. The quote is the evidence, not the "
			"conclusion."
		)
	)
	level: str = Field(
		description=(
			"Signal level: 'decision' for architecture/tech choices, "
			"'preference' for user preferences/working style, "
			"'feedback' for corrections or confirmations, "
			"'reference' for external system pointers, "
			"'implementation' for code details/debugging/task execution "
			"(implementation findings are noted for context but never become memories)"
		)
	)


# ── Deps ─────────────────────────────────────────────────────────────────


@dataclass
class ExtractDeps:
	"""Dependencies injected into every tool call.

	Holds paths AND per-run mutable reasoning state. Tools reach memory
	files, the session trace, and the run workspace via ctx.deps.memory_root
	/ ctx.deps.trace_path / ctx.deps.run_folder. Tools and history_processors
	share per-run state via:

	- ctx.deps.notes: list of Finding objects populated by the note() tool
	  during scanning. Visible to the agent via notes_state_injector's
	  NOTES system message each turn and via note() tool call arguments in
	  conversation history.
	- ctx.deps.pruned_offsets: set of trace offsets the agent has asked to
	  prune via the prune() tool. prune_history_processor reads this set
	  each turn and stubs matching read('trace', offset=X, ...) tool
	  returns to '[pruned]' to free context.

	Mutable fields use field(default_factory=...) so each run starts with
	fresh state — no cross-run leakage.
	"""

	memory_root: Path
	trace_path: Path | None = None
	run_folder: Path | None = None
	notes: list[Finding] = field(default_factory=list)
	pruned_offsets: set[int] = field(default_factory=set)


# ── Internal helpers (not tools) ─────────────────────────────────────────


def _resolve(deps: ExtractDeps, filename: str) -> Path | None:
	"""Resolve a filename to an absolute path within allowed roots.

	Returns None for empty filenames, path-traversal attempts, or an
	unconfigured trace. Not a tool.
	"""
	if not filename or not filename.strip():
		return None
	if filename in ("trace", "trace.jsonl"):
		return deps.trace_path
	# Summary files live in summaries/ subdir
	if filename.startswith("summary_"):
		path = (deps.memory_root / "summaries" / filename).resolve()
		if path.is_relative_to(deps.memory_root.resolve()):
			return path
		return None
	# Memory files, index.md — flat in memory_root
	path = (deps.memory_root / filename).resolve()
	if path.is_relative_to(deps.memory_root.resolve()):
		return path
	return None


def _find_occurrences(content: str, search: str) -> list[int]:
	"""Return start indices of all occurrences of search in content."""
	indices = []
	start = 0
	while True:
		idx = content.find(search, start)
		if idx == -1:
			break
		indices.append(idx)
		start = idx + 1
	return indices


def _index_to_line(content: str, index: int) -> int:
	"""Convert a character index to a 1-based line number."""
	return content[:index].count("\n") + 1


def _normalize_whitespace(text: str) -> str:
	"""Collapse each line's leading whitespace and strip trailing."""
	lines = text.split("\n")
	return "\n".join(
		re.sub(r"^[ \t]+", lambda m: " " * len(m.group().replace("\t", "    ")), line).rstrip()
		for line in lines
	)


# ── Tool functions (single source of truth) ─────────────────────────────


def read(ctx: RunContext[ExtractDeps], filename: str, offset: int = 0, limit: int = 0) -> str:
	"""Read a file with optional pagination. Returns content with line numbers.

	Use offset/limit to page through large files like session traces. For
	small files (memories, index.md), call with defaults to read the entire
	file. When reading the session trace, limit is hard-capped at 100 lines
	per call — paginate via offset to read more.

	Args:
		filename: File to read. Use "trace" for the session trace, or a
			memory filename like "feedback_tabs.md" or "index.md".
		offset: Line number to start from (0-based). Default 0.
		limit: Max lines to return. 0 means entire file (capped at 100 for
			trace reads). Default 0.
	"""
	deps = ctx.deps
	# Defensive: strip whitespace the model may append to the filename.
	if isinstance(filename, str):
		filename = filename.strip()
	path = _resolve(deps, filename)
	if path is None:
		if not filename:
			return (
				"Error: read() requires a filename argument. "
				"Examples: read('index.md'), "
				"read('trace', offset=0, limit=100), "
				"read('feedback_tabs.md'). "
				"Retry with one of these shapes."
			)
		if filename in ("trace", "trace.jsonl"):
			return "Error: no trace path configured"
		return f"Error: invalid filename: {filename!r}"
	if not path.exists():
		return f"Error: file not found: {filename}"
	if not path.is_file():
		return f"Error: not a file: {filename}"

	is_trace = filename in ("trace", "trace.jsonl")
	if is_trace:
		if limit <= 0 or limit > TRACE_MAX_LINES_PER_READ:
			limit = TRACE_MAX_LINES_PER_READ

	lines = path.read_text(encoding="utf-8").splitlines()
	total = len(lines)

	if limit > 0:
		chunk = lines[offset:offset + limit]

		# Byte caps — trace reads only. A single Claude trace line can be
		# 50KB+ (large tool outputs), so a raw 100-line chunk could be 5MB
		# and blow the model's context window after 2-3 turns. Truncate any
		# line over TRACE_MAX_LINE_BYTES, and stop adding lines once the
		# running total crosses TRACE_MAX_CHUNK_BYTES. The early break
		# shrinks len(chunk); the existing "more lines" header logic below
		# picks up the new next-offset naturally.
		if is_trace:
			safe_chunk: list[str] = []
			running_bytes = 0
			for line in chunk:
				if len(line) > TRACE_MAX_LINE_BYTES:
					dropped = len(line) - TRACE_MAX_LINE_BYTES
					line = (
						line[:TRACE_MAX_LINE_BYTES]
						+ f" ... [truncated {dropped} chars from this line]"
					)
				line_bytes = len(line.encode("utf-8"))
				if running_bytes + line_bytes > TRACE_MAX_CHUNK_BYTES:
					break
				safe_chunk.append(line)
				running_bytes += line_bytes
			chunk = safe_chunk

		numbered = [f"{offset + i + 1}\t{line}" for i, line in enumerate(chunk)]
		last_line = offset + len(chunk)
		header = f"[{total} lines, showing {offset + 1}-{last_line}]"
		if is_trace and last_line < total:
			header += (
				f" — {total - last_line} more lines, call "
				f"read('trace', offset={last_line}, limit={TRACE_MAX_LINES_PER_READ}) "
				f"for the next chunk"
			)
		return header + "\n" + "\n".join(numbered)

	# Full file read (non-trace files only)
	numbered = [f"{i + 1}\t{line}" for i, line in enumerate(lines)]
	return "\n".join(numbered)


def grep(ctx: RunContext[ExtractDeps], filename: str, pattern: str, context_lines: int = 2) -> str:
	"""Search a file for lines matching a regex pattern.

	Uses ripgrep internally for speed, falls back to Python regex if rg is
	not available. Returns up to 20 matches with context lines around each.

	Args:
		filename: File to search. Use "trace" for the session trace, or a
			memory filename like "feedback_tabs.md".
		pattern: Regex pattern to search for (case-insensitive).
		context_lines: Lines of context around each match. Default 2.
	"""
	deps = ctx.deps
	# Defensive: strip whitespace the model may append to the filename/pattern.
	if isinstance(filename, str):
		filename = filename.strip()
	if isinstance(pattern, str):
		pattern = pattern.strip()
	path = _resolve(deps, filename)
	if path is None:
		if not filename:
			return "Error: grep() requires a filename argument (e.g. 'trace' or 'index.md')"
		if filename in ("trace", "trace.jsonl"):
			return "Error: no trace path configured"
		return f"Error: invalid filename: {filename!r}"
	if not path.exists():
		return f"Error: file not found: {filename}"

	if shutil.which("rg"):
		try:
			result = subprocess.run(
				[
					"rg", "--no-heading", "-n", "-i",
					"-C", str(context_lines),
					"--max-count", "20",
					pattern, str(path),
				],
				capture_output=True, text=True, timeout=10,
			)
		except subprocess.TimeoutExpired:
			return f"Error: search timed out in {filename}"
		if result.returncode == 1:
			return f"No matches for '{pattern}' in {filename}"
		if result.returncode != 0:
			return f"Error: rg failed: {result.stderr.strip()}"
		return result.stdout.rstrip()

	# Python fallback
	lines = path.read_text(encoding="utf-8").splitlines()
	try:
		pat = re.compile(pattern, re.IGNORECASE)
	except re.error as exc:
		return f"Error: invalid regex: {exc}"
	matches = []
	for i, line in enumerate(lines):
		if pat.search(line):
			start = max(0, i - context_lines)
			end = min(len(lines), i + context_lines + 1)
			block = [f"{j + 1}\t{lines[j]}" for j in range(start, end)]
			matches.append("\n".join(block))
			if len(matches) >= 20:
				break
	if not matches:
		return f"No matches for '{pattern}' in {filename}"
	return "\n--\n".join(matches)


def scan(ctx: RunContext[ExtractDeps], directory: str = "", pattern: str = "*.md") -> str:
	"""List memory files or files in a subdirectory.

	For the memory root (directory=""), returns filename, description, and
	last modified time for each memory file. Filenames encode type and topic
	(e.g. feedback_use_tabs.md, project_migration.md). For subdirectories
	like "summaries" or "archived", returns filename and modified time.

	Returns JSON. Memory root: {count, memories: [{filename, description,
	modified}]}. Subdirectory: {count, files: [{filename, modified}]}.

	Args:
		directory: Subdirectory under memory root to scan. "" for the memory
			root itself. Use "summaries" or "archived" for those.
		pattern: Glob pattern to match files. Default "*.md".
	"""
	deps = ctx.deps
	scan_dir = deps.memory_root / directory if directory else deps.memory_root
	if not scan_dir.is_dir():
		return json.dumps({"count": 0, "memories": []})

	files = sorted(scan_dir.glob(pattern))

	# Rich manifest for memory root (files with frontmatter)
	if not directory:
		memories = []
		for f in files:
			if f.name == "index.md":
				continue
			try:
				post = fm_lib.load(str(f))
				mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
				memories.append({
					"filename": f.name,
					"description": post.get("description", ""),
					"modified": mtime.strftime("%Y-%m-%d %H:%M"),
				})
			except Exception:
				mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
				memories.append({
					"filename": f.name,
					"description": "",
					"modified": mtime.strftime("%Y-%m-%d %H:%M"),
				})
		return json.dumps({"count": len(memories), "memories": memories}, indent=2)

	# Subdirectory listing with modified time
	file_list = []
	for f in files:
		if f.is_file():
			mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
			file_list.append({
				"filename": f.name,
				"modified": mtime.strftime("%Y-%m-%d %H:%M"),
			})
	return json.dumps({"count": len(file_list), "files": file_list}, indent=2)


def verify_index(ctx: RunContext[ExtractDeps], filename: str = "index.md") -> str:
	"""Check if index.md is consistent with actual memory files.

	Compares memory files on disk against entries in index.md. Returns "OK"
	if consistent, or a report listing missing entries, stale entries,
	broken links, and duplicates so you can fix them with edit("index.md").
	After fixing, call read("index.md") for a final format check.

	Args:
		filename: Index file to verify. Default "index.md".
	"""
	deps = ctx.deps
	# Defensive: strip whitespace the model may append.
	if isinstance(filename, str):
		filename = filename.strip() or "index.md"
	index_path = deps.memory_root / "index.md"
	md_files = sorted(deps.memory_root.glob("*.md"))
	memory_files = {f.name for f in md_files if f.name != "index.md"}

	# Parse index entries — look for markdown links: [Title](filename.md)
	index_entry_list: list[str] = []
	if index_path.exists():
		for line in index_path.read_text(encoding="utf-8").splitlines():
			match = re.search(r"\]\(([^)]+\.md)\)", line)
			if match:
				index_entry_list.append(match.group(1))
	index_entries: set[str] = set(index_entry_list)

	# Detect duplicate entries (same filename linked more than once)
	seen: set[str] = set()
	duplicates: set[str] = set()
	for entry in index_entry_list:
		if entry in seen:
			duplicates.add(entry)
		seen.add(entry)

	missing_from_index = memory_files - index_entries
	stale_in_index = index_entries - memory_files

	# Verify every linked file actually exists on disk. Resolve each index
	# entry relative to memory_root (handles both flat files like
	# "feedback_tabs.md" and paths like "summaries/file.md").
	broken_links: set[str] = set()
	for entry in index_entries:
		resolved = (deps.memory_root / entry).resolve()
		if not resolved.is_file():
			broken_links.add(entry)

	if not missing_from_index and not stale_in_index and not broken_links and not duplicates:
		return f"OK: index.md is consistent ({len(memory_files)} files, {len(index_entries)} entries)"

	parts = ["NOT OK:"]
	if missing_from_index:
		for fname in sorted(missing_from_index):
			desc = ""
			try:
				post = fm_lib.load(str(deps.memory_root / fname))
				desc = post.get("description", "")
			except Exception:
				pass
			parts.append(f"  Missing from index: {fname} — {desc}" if desc else f"  Missing from index: {fname}")
	if stale_in_index:
		for fname in sorted(stale_in_index):
			parts.append(f"  Stale in index (file not found): {fname}")
	if broken_links:
		for fname in sorted(broken_links):
			parts.append(f"  Broken link (file missing on disk): {fname}")
	if duplicates:
		for fname in sorted(duplicates):
			parts.append(f"  Duplicate entry in index: {fname}")
	return "\n".join(parts)


# ── Reasoning-state tools (note, prune) ─────────────────────────────────
#
# These two tools don't touch disk. They manage the agent's in-run
# reasoning state (ctx.deps.notes, ctx.deps.pruned_offsets) so the agent
# can accumulate structured findings while dynamically freeing context.
# History processors (notes_state_injector, prune_history_processor)
# consume this state on each subsequent turn to keep the agent grounded.


# Short stub string used by prune_history_processor to replace pruned
# trace-chunk tool returns. Kept deliberately tiny (~2 tokens) because
# every subsequent turn pays for every pruned stub in context.
PRUNED_STUB = "[pruned]"


def note(ctx: RunContext[ExtractDeps], findings: list[Finding]) -> str:
	"""Record structured findings from the trace chunks you just read.

	Call this after reading one or more trace chunks to capture extractable
	signal before pruning those chunks. Findings accumulate in your persistent
	reasoning state and stay visible every turn via the NOTES system message.
	You can batch multiple findings from several chunks in a single call — do
	that whenever natural to save a tool turn.

	After you note() a chunk's findings, it's safe to prune() that chunk to
	free context. The finding's `offset` field lets you re-read the exact
	trace lines later if you need the full context when writing the memory.

	Args:
		findings: List of Finding(theme, offset, quote) objects. `theme` is
			a short label like 'DSPy migration' or 'config refactor' that
			groups related findings. `offset` is the trace line where the
			evidence appears (use the offset you just read from).  `quote`
			is a short verbatim snippet from the trace (under ~200 chars)
			that supports the finding.
	"""
	deps = ctx.deps
	deps.notes.extend(findings)
	total = len(deps.notes)
	return f"Noted {len(findings)} findings (total {total} so far)."


def prune(ctx: RunContext[ExtractDeps], trace_offsets: list[int]) -> str:
	"""Drop trace-chunk read results from context to free tokens.

	Call this when the CONTEXT: system message shows soft or hard pressure
	(>60% or >80% usage) AND you have already captured findings from those
	chunks via note(). Pruning replaces the results of matching
	read('trace', offset=X, ...) calls with a short '[pruned]' stub. The
	tool CALLS themselves remain visible so you still know 'I already read
	offset X' — only the chunk CONTENT is discarded.

	Pruning is NOT destructive to evidence: if you later need the full text
	of a pruned chunk (e.g. to extract a verbatim quote for a write() call),
	you can always call read('trace', offset=X, limit=N) again. Your notes
	preserve the finding themes and offsets even when chunks are pruned.

	Only trace-chunk reads can be pruned. index.md, memory files, scan()
	results, and grep() results are never touched.

	Args:
		trace_offsets: Offsets of prior read('trace', offset=X, limit=N)
			calls whose results should be stubbed. Example: [0, 100, 200]
			prunes the first three chunk reads. You can batch several
			offsets in one call.
	"""
	deps = ctx.deps
	if not trace_offsets:
		return "No offsets to prune."
	before = len(deps.pruned_offsets)
	deps.pruned_offsets.update(trace_offsets)
	added = len(deps.pruned_offsets) - before
	return (
		f"Pruned {added} new offset(s) (requested {len(trace_offsets)}; "
		f"total pruned: {len(deps.pruned_offsets)})."
	)


def write(ctx: RunContext[ExtractDeps], type: str, name: str, description: str, body: str) -> str:
	"""Create a new memory or summary file.

	Auto-generates the filename from type and name. If a file with the
	generated name already exists, returns an error — use read() then
	edit() to update existing files. For summaries (type="summary"), the
	file is written to the summaries/ subdirectory with a timestamped
	filename.

	Before writing, verify your body against the memory format rules:
	- feedback/project bodies MUST use **Why:** and **How to apply:**
	  (inline bold, NOT ## headings — headings are for summaries only)
	- No file paths like src/foo.py — use conceptual descriptions
	- Max 20 lines in body — one topic per memory
	- Content must not be code-derivable (git log, README, etc.)

	Args:
		type: One of "user", "feedback", "project", "reference", "summary".
		name: Short title, max ~10 words. Used to generate the filename.
		description: One-line retrieval hook, ~150 chars.
		body: Content. For feedback/project: rule, then **Why:**, then
			**How to apply:**. For summary: ## User Intent and ## What
			Happened sections.
	"""
	deps = ctx.deps

	# --- Defensive arg normalization ---
	# Models (esp. MiniMax-M2.5) occasionally serialize tool-call string args
	# with trailing newlines or padding whitespace. Before v5 this caused
	# spurious validation rejections where type="summary\n" wasn't in
	# MEMORY_TYPES and the model had to burn a retry. Stripping at the tool
	# boundary turns those failures into silent successes.
	if isinstance(type, str):
		type = type.strip()
	if isinstance(name, str):
		name = name.strip()
	if isinstance(description, str):
		description = description.strip()
	# body stays untouched — it's multi-line markdown and trailing newlines
	# inside the body are semantically meaningful.

	# --- Validate ---
	if type not in MEMORY_TYPES:
		return f"Error: type must be one of {MEMORY_TYPES}, got {type!r}"
	if not name:
		return "Error: name cannot be empty"
	if not description:
		return "Error: description cannot be empty"
	if not body or not body.strip():
		return "Error: body cannot be empty"

	# --- Warn about code-level content in memory bodies ---
	if type != "summary":
		path_refs = re.findall(r'(?:src/|tests/|\.py\b|\.ts\b|\.js\b|function\s+\w+\()', body)
		if len(path_refs) >= 2:
			return (
				f"Warning: body contains {len(path_refs)} code-level references "
				f"({', '.join(path_refs[:3])}). Memories should describe decisions "
				f"and preferences at a conceptual level, not reference specific files "
				f"or functions. Rewrite the body without file paths, or skip this "
				f"memory if the content is code-derivable."
			)

	# --- Require Why/How sections in feedback and project memories ---
	if type in ("feedback", "project"):
		has_why = "**Why:**" in body or "**why:**" in body
		has_how = "**How to apply:**" in body or "**how to apply:**" in body
		if not has_why or not has_how:
			missing = []
			if not has_why:
				missing.append("**Why:**")
			if not has_how:
				missing.append("**How to apply:**")
			return (
				f"Warning: {type} memory body is missing {' and '.join(missing)}. "
				f"feedback/project memories must include **Why:** (rationale) and "
				f"**How to apply:** (concrete action). Add these sections or skip "
				f"this memory."
			)

	# --- Generate filename and target path ---
	slug = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")[:128]

	if type == "summary":
		timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
		filename = f"{timestamp}_{slug}.md"
		target_dir = deps.memory_root / "summaries"
	else:
		filename = f"{type}_{slug}.md"
		target_dir = deps.memory_root

	target = target_dir / filename

	# --- If file exists, tell model to STOP writing this topic ---
	# Sharpened 2026-04-11 after smoke showed the agent retrying write()
	# with slug variants (feedback_foo → feedback_foo_v2 → ...) when it
	# saw "already exists". The new wording is terminal: stop for this
	# topic, either read+edit or skip. Retry with a slug variant is a bug.
	if target.exists():
		return (
			f"Error: '{filename}' already exists. STOP writing this topic. "
			f"Do NOT call write() again with this name OR a similar slug variant. "
			f"Your two allowed next actions: "
			f"(1) read(\"{filename}\") + edit(\"{filename}\", ...) to update it, "
			f"OR (2) skip this candidate and move to the next memory. "
			f"Retrying write() with any variant of this name is a bug."
		)

	# --- Build frontmatter + body ---
	# yaml.safe_dump auto-quotes any value containing YAML specials (':', '#',
	# '"', '[', leading '-', '|', '>', etc.) and handles unicode. Hand-built
	# f-strings here previously crashed frontmatter.load() whenever the model
	# wrote a description with a colon.
	frontmatter_yaml = yaml.safe_dump(
		{
			"name": name.strip(),
			"description": description.strip(),
			"type": type,
		},
		default_flow_style=False,
		sort_keys=False,
		allow_unicode=True,
	)
	content = f"---\n{frontmatter_yaml}---\n\n{body.strip()}\n"

	target.parent.mkdir(parents=True, exist_ok=True)
	target.write_text(content, encoding="utf-8")
	# For summaries, return the relative path including the summaries/
	# prefix so index.md links resolve correctly.
	index_filename = f"summaries/{filename}" if type == "summary" else filename
	return json.dumps({
		"filename": index_filename,
		"bytes": len(content.encode("utf-8")),
		"type": type,
	})


def edit(
	ctx: RunContext[ExtractDeps],
	filename: str,
	old_string: str,
	new_string: str,
	near_line: int = 0,
) -> str:
	"""Replace old_string with new_string in a file. Surgical edit.

	Three-phase matching: (1) exact match on old_string, (2) fuzzy fallback
	that normalizes whitespace and retries, (3) if multiple matches,
	near_line (1-based) picks the closest. Use read() first to see line
	numbers, then edit with near_line for disambiguation when needed.

	Args:
		filename: File to edit (e.g. "feedback_tabs.md" or "index.md").
		old_string: The exact text to find and replace.
		new_string: The replacement text.
		near_line: Hint for disambiguation when multiple matches exist.
			1-based line number. 0 means no hint. Default 0.
	"""
	deps = ctx.deps
	# Defensive: strip whitespace the model may append to the filename.
	if isinstance(filename, str):
		filename = filename.strip()
	path = _resolve(deps, filename)
	if path is None:
		if not filename:
			return "Error: edit() requires a filename argument (e.g. 'feedback_tabs.md' or 'index.md')"
		return f"Error: invalid filename: {filename!r}"
	if not path.exists():
		return f"Error: file not found: {filename}"

	content = path.read_text(encoding="utf-8")

	# --- Phase 1: exact match ---
	indices = _find_occurrences(content, old_string)

	# --- Phase 2: fuzzy whitespace fallback ---
	fuzzy = False
	if not indices:
		norm_content = _normalize_whitespace(content)
		norm_search = _normalize_whitespace(old_string)
		indices = _find_occurrences(norm_content, norm_search)
		if indices:
			fuzzy = True
			content = norm_content
			old_string = norm_search
		else:
			return f"Error: old_string not found in {filename}"

	# --- Phase 3: disambiguation ---
	if len(indices) == 1:
		chosen = indices[0]
	elif near_line > 0:
		chosen = min(
			indices,
			key=lambda idx: abs(_index_to_line(content, idx) - near_line),
		)
	else:
		lines = [_index_to_line(content, i) for i in indices]
		return (
			f"Error: old_string matches {len(indices)} locations "
			f"(lines {lines}). Provide near_line to disambiguate."
		)

	# --- Phase 4: apply ---
	edit_line = _index_to_line(content, chosen)

	if fuzzy:
		# Re-read original, do line-level replacement
		original = path.read_text(encoding="utf-8")
		old_lines = old_string.split("\n")
		orig_lines = original.split("\n")
		new_lines = new_string.split("\n")
		start = edit_line - 1
		end = start + len(old_lines)
		orig_lines[start:end] = new_lines
		new_content = "\n".join(orig_lines)
	else:
		new_content = content[:chosen] + new_string + content[chosen + len(old_string):]

	path.write_text(new_content, encoding="utf-8")
	return f"Edited {filename} at line {edit_line}"


def archive(ctx: RunContext[ExtractDeps], filename: str) -> str:
	"""Soft-delete a memory by moving it to the archived/ subdirectory.

	The file is not permanently deleted — it can be restored by moving it
	back. Only .md files in the memory root can be archived (not index.md).

	Args:
		filename: Memory filename to archive (e.g. "feedback_old.md").
	"""
	deps = ctx.deps
	path = _resolve(deps, filename)
	if path is None:
		if not filename or not filename.strip():
			return "Error: filename cannot be empty"
		return f"Error: invalid filename: {filename}"
	if not path.exists():
		return f"Error: file not found: {filename}"
	if path.suffix != ".md":
		return f"Error: only .md files can be archived, got: {filename}"
	if filename == "index.md":
		return "Error: cannot archive index.md"

	archive_dir = deps.memory_root / "archived"
	archive_dir.mkdir(parents=True, exist_ok=True)
	target = archive_dir / path.name
	shutil.move(str(path), str(target))
	return json.dumps({"archived": filename, "moved_to": f"archived/{filename}"})


# ── Budget helper and history_processors ────────────────────────────────
#
# compute_request_budget() scales the per-run agent.run_sync() request_limit
# with trace size — short traces get a small budget, 2000-line traces get
# ~45 turns, pathological traces clamp at 80.
#
# The three history_processors are plain module-level functions with
# signature (ctx: RunContext[ExtractDeps], messages: list[ModelMessage]) ->
# list[ModelMessage]. PydanticAI 1.70.0's dispatcher inspects the first
# parameter type and passes RunContext when it sees the annotation — so
# these functions can read ctx.deps.notes and ctx.deps.pruned_offsets
# directly without any closure factory plumbing.
#
# Processor order matters for the system-message injection order as the
# agent sees them: agent.run_sync(history_processors=[context_pressure,
# notes_state, prune_rewriter]) injects CONTEXT first, then NOTES, then
# does the prune rewrite.


def compute_request_budget(trace_path: Path) -> int:
	"""Scale the agent's request_limit with trace size.

	Formula: 25 base + 2 * chunks_needed, clamped to [50, 100].

	Base (25) covers: orient, grep, a few synthesis thinking turns,
	verify_index, 2-4 writes, the session summary, and a few retry
	slots (Agent's ``retries=3`` means a flubbed tool call can burn
	up to 3 slots of the same "effective" action). Per-chunk factor
	of 2 covers both the ``read`` AND its follow-up
	``note``/``prune`` activity. Floor 50 gives the smallest traces
	comfortable headroom without being wasteful.

	Floor 50 was empirically established during smoke testing:
	20-floor traces hit ``UsageLimitExceeded`` on 137- and 157-line
	inputs despite their tiny size, because the agent goes through
	the full scan/note/prune ceremony and ``retries=3`` amplifies
	every flubbed tool call. 30-floor also turned out tight with the
	retry buffer; 50 is the new safe floor.

	Examples:
		100-line trace  (1 chunk)  → max(50, 27) = 50 (floor)
		500-line trace  (5 chunks) → max(50, 35) = 50 (floor)
		1000-line trace (10 chunks)→ max(50, 45) = 50 (floor)
		2000-line trace (20 chunks)→ max(50, 65) = 65
		2165-line trace (21 chunks)→ max(50, 67) = 67
		5000-line trace (50 chunks)→ min(100, 125) = 100 (ceiling)

	Args:
		trace_path: Path to the session trace .jsonl file. Line count
			determines the chunk budget. Unreadable files fall back to
			a safe 100-line estimate so the agent still gets a budget.
	"""
	try:
		with trace_path.open("r", encoding="utf-8") as fh:
			lines = sum(1 for _ in fh)
	except (OSError, UnicodeDecodeError):
		lines = 100
	chunks_needed = max(1, lines // TRACE_MAX_LINES_PER_READ)
	raw = 25 + 2 * chunks_needed
	return max(50, min(100, raw))


def _estimate_message_tokens(messages: list[ModelMessage]) -> int:
	"""Rough token count for a message list via ~4-char/token heuristic.

	Not exact — real tokenizers vary ±20% — but stable and fast enough for
	soft/hard pressure thresholding. Rounds UP (via ceiling) because over-
	estimating is safer than under-estimating here: we want the agent to
	see pressure slightly earlier than strictly necessary.
	"""
	total_chars = 0
	for msg in messages:
		for part in msg.parts:
			# String-bearing fields across the message part dataclasses.
			content = getattr(part, "content", None)
			if isinstance(content, str):
				total_chars += len(content)
			elif isinstance(content, list):
				for item in content:
					if isinstance(item, str):
						total_chars += len(item)
					else:
						# Best-effort for non-str user content.
						total_chars += len(str(item))
			args = getattr(part, "args", None)
			if isinstance(args, str):
				total_chars += len(args)
			elif isinstance(args, dict):
				total_chars += len(json.dumps(args))
	return int(total_chars * _TOKENS_PER_CHAR) + 1


def _inject_system_message(messages: list[ModelMessage], label: str) -> None:
	"""Append a fresh SystemPromptPart with `label` to the last ModelRequest.

	The last ModelRequest is the one about to be sent to the model, so
	injecting there guarantees the agent sees the status on THIS turn (not
	next turn). Mutates `messages` in place; caller is expected to return
	the same list.
	"""
	target: ModelRequest | None = None
	for msg in reversed(messages):
		if isinstance(msg, ModelRequest):
			target = msg
			break
	if target is None:
		return
	target.parts.append(
		SystemPromptPart(
			content=label,
			timestamp=datetime.now(timezone.utc),
		)
	)


def context_pressure_injector(
	ctx: RunContext[ExtractDeps],
	messages: list[ModelMessage],
) -> list[ModelMessage]:
	"""Inject a CONTEXT: status line with graduated pressure warnings.

	Fires every turn. The agent always sees how close it is to the context
	limit. Soft pressure at 60%+ suggests pruning; hard pressure at 80%+
	demands it before any further read(). No force-eviction — the agent is
	responsible for acting on the pressure (per plan phase one). If
	experimentation shows the agent ignores hard pressure, a phase-two
	safety net can be added.
	"""
	tokens = _estimate_message_tokens(messages)
	pct = tokens / MODEL_CONTEXT_TOKEN_LIMIT
	if pct > CONTEXT_HARD_PRESSURE_PCT:
		label = (
			f"CONTEXT: {tokens:,}/{MODEL_CONTEXT_TOKEN_LIMIT:,} "
			f"({pct:.0%}) — HARD PRESSURE: prune(trace_offsets=[...]) "
			f"BEFORE your next read('trace', ...)"
		)
	elif pct > CONTEXT_SOFT_PRESSURE_PCT:
		label = (
			f"CONTEXT: {tokens:,}/{MODEL_CONTEXT_TOKEN_LIMIT:,} "
			f"({pct:.0%}) — soft pressure: consider prune() on chunks "
			f"you've already captured via note()"
		)
	else:
		label = (
			f"CONTEXT: {tokens:,}/{MODEL_CONTEXT_TOKEN_LIMIT:,} ({pct:.0%})"
		)
	_inject_system_message(messages, label)
	return messages


def notes_state_injector(
	ctx: RunContext[ExtractDeps],
	messages: list[ModelMessage],
) -> list[ModelMessage]:
	"""Inject a NOTES: status line summarizing ctx.deps.notes each turn.

	Gives the agent an always-fresh view of its accumulated findings
	without scanning conversation history. Groups findings by theme with
	counts so the agent sees the rough shape of its own synthesis as it
	scans the trace.

	Reads live state from ctx.deps.notes — mutation by note() in turn N
	is visible to this injector in turn N+1 (confirmed on scratch
	validation script).
	"""
	notes = ctx.deps.notes
	if not notes:
		label = "NOTES: 0 findings (scan phase in progress)"
	else:
		theme_counts: Counter[str] = Counter(f.theme for f in notes)
		theme_list = ", ".join(
			f"'{theme}' ({count})" for theme, count in theme_counts.most_common()
		)
		n_impl = sum(
			1 for f in notes
			if hasattr(f, "level") and (
				"implementation" in (f.level or "").lower()
				or "impl" in (f.level or "").lower()
			)
		)
		n_durable = len(notes) - n_impl
		label = (
			f"NOTES: {len(notes)} findings ({n_durable} durable, "
			f"{n_impl} implementation) across "
			f"{len(theme_counts)} theme(s) — {theme_list}"
		)
	_inject_system_message(messages, label)
	return messages


def prune_history_processor(
	ctx: RunContext[ExtractDeps],
	messages: list[ModelMessage],
) -> list[ModelMessage]:
	"""Rewrite trace-read tool returns to '[pruned]' for offsets marked by prune().

	Two-pass walk over `messages`:
	  1. Collect tool_call_ids of read() calls where filename is 'trace'/
	     'trace.jsonl' AND the call's offset argument is in
	     ctx.deps.pruned_offsets.
	  2. Rewrite the matching ToolReturnPart.content in place to PRUNED_STUB.

	Preserves ToolCallPart intact so the agent still sees 'I already read
	offset X' (the call record is the record of action). Only the chunk
	TEXT is discarded. OpenAI chat format invariant: every tool call must
	have a matching tool return with a non-empty content string —
	PRUNED_STUB satisfies this.

	Non-trace reads (index.md, memory files, scan, grep) are never touched.
	"""
	deps = ctx.deps
	if not deps.pruned_offsets:
		return messages

	# Pass 1: collect tool_call_ids of pruneable read-trace calls.
	pruned_ids: set[str] = set()
	for msg in messages:
		if not isinstance(msg, ModelResponse):
			continue
		for part in msg.parts:
			if not isinstance(part, ToolCallPart):
				continue
			if part.tool_name != "read":
				continue
			args: Any = part.args
			if isinstance(args, str):
				try:
					args = json.loads(args)
				except json.JSONDecodeError:
					continue
			if not isinstance(args, dict):
				continue
			if args.get("filename") not in ("trace", "trace.jsonl"):
				continue
			offset = args.get("offset", 0)
			if not isinstance(offset, int):
				continue
			if offset in deps.pruned_offsets:
				pruned_ids.add(part.tool_call_id)

	if not pruned_ids:
		return messages

	# Pass 2: rewrite matching ToolReturnPart.content in place.
	for msg in messages:
		if not isinstance(msg, ModelRequest):
			continue
		for part in msg.parts:
			if isinstance(part, ToolReturnPart) and part.tool_call_id in pruned_ids:
				part.content = PRUNED_STUB

	return messages


# Test/compat helper: construct a minimal context wrapper exposing ctx.deps.
def build_test_ctx(
	memory_root: Path,
	trace_path: Path | None = None,
	run_folder: Path | None = None,
):
	"""Return a lightweight RunContext-like object for direct tool calls in tests."""
	deps = ExtractDeps(
		memory_root=Path(memory_root),
		trace_path=Path(trace_path) if trace_path else None,
		run_folder=Path(run_folder) if run_folder else None,
	)
	return SimpleNamespace(deps=deps)


# ── Legacy compatibility shim (DSPy maintain/ask agents only) ────────────


class MemoryTools:
	"""Thin compatibility adapter for legacy DSPy agents (maintain, ask).

	The class exists solely so `MaintainAgent` and `AskAgent` can continue
	passing bound methods to `dspy.ReAct(tools=[self.tools.read, ...])`
	without being rewritten for the PydanticAI tool signature. Each method
	forwards to the module-level function with a synthetic `RunContext`.

	Deprecated: will be removed when maintain and ask migrate to PydanticAI.
	New PydanticAI code must use the module-level functions directly via
	`Agent(tools=[read, grep, scan, write, edit, verify_index])`.
	"""

	def __init__(
		self,
		memory_root: Path,
		trace_path: Path | None = None,
		run_folder: Path | None = None,
	):
		self._deps = ExtractDeps(
			memory_root=Path(memory_root),
			trace_path=Path(trace_path) if trace_path else None,
			run_folder=Path(run_folder) if run_folder else None,
		)
		# Synthetic RunContext-like object so the module functions can be
		# called with ctx.deps access. PydanticAI's RunContext has more
		# attributes but our tools only read ctx.deps, so SimpleNamespace
		# is sufficient.
		self._ctx = SimpleNamespace(deps=self._deps)

	# Path accessors for callers that still need raw paths.
	@property
	def memory_root(self) -> Path:
		return self._deps.memory_root

	@property
	def trace_path(self) -> Path | None:
		return self._deps.trace_path

	@property
	def run_folder(self) -> Path | None:
		return self._deps.run_folder

	# Tool method forwarders — each is a one-line delegation to the module
	# function. Method signature matches the module function signature
	# minus the ctx parameter (which is supplied from self._ctx).

	def read(self, filename: str, offset: int = 0, limit: int = 0) -> str:
		return read(self._ctx, filename, offset, limit)

	def grep(self, filename: str, pattern: str, context_lines: int = 2) -> str:
		return grep(self._ctx, filename, pattern, context_lines)

	def scan(self, directory: str = "", pattern: str = "*.md") -> str:
		return scan(self._ctx, directory, pattern)

	def verify_index(self, filename: str = "index.md") -> str:
		return verify_index(self._ctx, filename)

	def write(self, type: str, name: str, description: str, body: str) -> str:
		return write(self._ctx, type, name, description, body)

	def edit(
		self,
		filename: str,
		old_string: str,
		new_string: str,
		near_line: int = 0,
	) -> str:
		return edit(self._ctx, filename, old_string, new_string, near_line)

	def archive(self, filename: str) -> str:
		return archive(self._ctx, filename)
