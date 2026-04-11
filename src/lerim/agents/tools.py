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
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import frontmatter as fm_lib
import yaml
from pydantic_ai import RunContext

MEMORY_TYPES = ("user", "feedback", "project", "reference", "summary")

# Hard caps on read("trace", ...) output. The line-count cap forces
# chunked reads (agent paginates via offset) so trajectories stay bounded.
# The byte caps protect the model's context window from single huge trace
# lines (e.g. massive tool results) and runaway chunk payloads that would
# otherwise blow the input limit. Memory-file reads are unbounded — memory
# files are small by design.
TRACE_MAX_LINES_PER_READ = 100
TRACE_MAX_LINE_BYTES = 5_000       # per-line truncation cap
TRACE_MAX_CHUNK_BYTES = 50_000     # total chunk payload cap


# ── Deps ─────────────────────────────────────────────────────────────────


@dataclass
class ExtractDeps:
	"""Dependencies injected into every tool call.

	Holds paths only. Tools reach memory files, the session trace, and the
	run workspace via ctx.deps.memory_root / ctx.deps.trace_path /
	ctx.deps.run_folder — never via a class instance.
	"""

	memory_root: Path
	trace_path: Path | None = None
	run_folder: Path | None = None


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
	path = _resolve(deps, filename)
	if path is None:
		if not filename or not filename.strip():
			return "Error: filename cannot be empty"
		if filename in ("trace", "trace.jsonl"):
			return "Error: no trace path configured"
		return f"Error: invalid filename: {filename}"
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
	path = _resolve(deps, filename)
	if path is None:
		if not filename or not filename.strip():
			return "Error: filename cannot be empty"
		if filename in ("trace", "trace.jsonl"):
			return "Error: no trace path configured"
		return f"Error: invalid filename: {filename}"
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

	# --- Validate ---
	if type not in MEMORY_TYPES:
		return f"Error: type must be one of {MEMORY_TYPES}, got '{type}'"
	if not name or not name.strip():
		return "Error: name cannot be empty"
	if not description or not description.strip():
		return "Error: description cannot be empty"
	if not body or not body.strip():
		return "Error: body cannot be empty"

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

	# --- If file exists, tell model to use read + edit ---
	if target.exists():
		return (
			f"Error: '{filename}' already exists. "
			f"Use read(\"{filename}\") to see its content, "
			f"then edit(\"{filename}\", old_string, new_string) to update it."
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
	path = _resolve(deps, filename)
	if path is None:
		if not filename or not filename.strip():
			return "Error: filename cannot be empty"
		return f"Error: invalid filename: {filename}"
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
