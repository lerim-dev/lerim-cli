"""MemoryTools — class-based tools for Lerim agents.

All tools are methods of MemoryTools. Context (paths, memory root, trace path)
lives in __init__. The model never sees or generates absolute paths.

Each agent picks which methods to use:
    extract_tools  = [tools.read, tools.grep, tools.scan, tools.write, tools.edit]
    maintain_tools = [tools.read, tools.scan, tools.write, tools.edit, tools.archive]
    ask_tools      = [tools.read, tools.scan]
"""

from __future__ import annotations

import re
from pathlib import Path

MEMORY_TYPES = ("user", "feedback", "project", "reference", "summary")


class MemoryTools:
	"""Tools for memory extraction, maintenance, and retrieval.

	Instantiate once per run with the relevant paths, then pass selected
	bound methods to dspy.ReAct. The model sees only the method arguments
	(filename, offset, pattern, etc.) — never absolute paths.
	"""

	def __init__(
		self,
		memory_root: Path,
		trace_path: Path | None = None,
		run_folder: Path | None = None,
	):
		self.memory_root = Path(memory_root)
		self.trace_path = Path(trace_path) if trace_path else None
		self.run_folder = Path(run_folder) if run_folder else None

	def _resolve(self, filename: str) -> Path | None:
		"""Resolve a filename to an absolute path. Not a tool."""
		if filename in ("trace", "trace.jsonl"):
			return self.trace_path
		# Summary files live in summaries/ subdir
		if filename.startswith("summary_"):
			path = self.memory_root / "summaries" / filename
			if path.exists():
				return path
		# Memory files, index.md — flat in memory_root
		return self.memory_root / filename

	# ── Read ────────────────────────────────────────────────────────────

	def read(self, filename: str, offset: int = 0, limit: int = 0) -> str:
		"""Read a file with optional pagination. Returns content with line numbers.

		Use offset/limit to page through large files like session traces.
		For small files (memories, index.md), call with defaults to read the
		entire file.

		Args:
			filename: File to read. Use "trace" for the session trace, or a
				memory filename like "feedback_tabs.md" or "index.md".
			offset: Line number to start from (0-based). Default 0.
			limit: Max lines to return. 0 means entire file. Default 0.
		"""
		path = self._resolve(filename)
		if path is None:
			return "Error: no trace path configured"
		if not path.exists():
			return f"Error: file not found: {filename}"
		if not path.is_file():
			return f"Error: not a file: {filename}"

		lines = path.read_text(encoding="utf-8").splitlines()
		total = len(lines)

		if limit > 0:
			chunk = lines[offset:offset + limit]
			numbered = [f"{offset + i + 1}\t{line}" for i, line in enumerate(chunk)]
			header = f"[{total} lines, showing {offset + 1}-{offset + len(chunk)}]"
			return header + "\n" + "\n".join(numbered)

		# Full file read
		numbered = [f"{i + 1}\t{line}" for i, line in enumerate(lines)]
		return "\n".join(numbered)

	# ── Grep (ripgrep) ─────────────────────────────────────────────────

	def grep(self, filename: str, pattern: str, context_lines: int = 2) -> str:
		"""Search a file for lines matching a regex pattern.

		Uses ripgrep internally for speed. Falls back to Python regex
		if rg is not available.

		Args:
			filename: File to search. Use "trace" for the session trace,
				or a memory filename like "feedback_tabs.md".
			pattern: Regex pattern to search for (case-insensitive).
			context_lines: Lines of context around each match. Default 2.
		"""
		import shutil
		import subprocess

		path = self._resolve(filename)
		if path is None:
			return "Error: no trace path configured"
		if not path.exists():
			return f"Error: file not found: {filename}"

		if shutil.which("rg"):
			result = subprocess.run(
				[
					"rg", "--no-heading", "-n", "-i",
					"-C", str(context_lines),
					"--max-count", "20",
					pattern, str(path),
				],
				capture_output=True, text=True, timeout=10,
			)
			if result.returncode == 1:
				return f"No matches for '{pattern}' in {filename}"
			if result.returncode != 0:
				return f"Error: rg failed: {result.stderr.strip()}"
			return result.stdout.rstrip()

		# Python fallback
		import re
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
				block = [
					f"{j + 1}\t{lines[j]}" for j in range(start, end)
				]
				matches.append("\n".join(block))
				if len(matches) >= 20:
					break
		if not matches:
			return f"No matches for '{pattern}' in {filename}"
		return "\n--\n".join(matches)


	# ── Scan ────────────────────────────────────────────────────────────

	def scan(self, directory: str = "", pattern: str = "*.md") -> str:
		"""List memory files or files in a subdirectory.

		For the memory root (directory=""), returns filename, description,
		and last modified time for each memory file. Filenames encode
		type and topic (e.g. feedback_use_tabs.md, project_migration.md).
		For subdirectories like "summaries" or "archived", returns
		filename and modified time.

		Returns JSON:
		  Memory root: {count, memories: [{filename, description, modified}]}
		  Subdirectory: {count, files: [{filename, modified}]}

		Args:
			directory: Subdirectory under memory root to scan.
				"" for the memory root itself. Use "summaries" or
				"archived" for those subdirectories.
			pattern: Glob pattern to match files. Default "*.md".
		"""
		import json
		from datetime import datetime, timezone

		import frontmatter as fm_lib

		scan_dir = self.memory_root / directory if directory else self.memory_root
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


	# ── Write ───────────────────────────────────────────────────────────

	def write(self, type: str, name: str, description: str, body: str) -> str:
		"""Create a new file. If a file with this name already exists,
		returns an error — use read() then edit() to update existing files.

		Auto-generates filename from type and name.
		Frontmatter (name, description, type) is generated internally.

		For summaries, use type="summary" — the file is written to the
		summaries/ subdirectory.

		Args:
			type: One of "user", "feedback", "project", "reference",
				"summary".
			name: Short title, max ~10 words. Used to generate filename.
			description: One-line retrieval hook, ~150 chars.
			body: Content. For feedback/project: rule, then Why, then
				How to apply. For summary: ## User Intent and
				## What Happened sections.
		"""
		import json

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
		from datetime import datetime, timezone
		slug = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")

		if type == "summary":
			timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
			filename = f"{timestamp}_{slug}.md"
			target_dir = self.memory_root / "summaries"
		else:
			filename = f"{type}_{slug}.md"
			target_dir = self.memory_root

		target = target_dir / filename

		# --- If file exists, tell model to use read + edit ---
		if target.exists():
			return (
				f"Error: '{filename}' already exists. "
				f"Use read(\"{filename}\") to see its content, "
				f"then edit(\"{filename}\", old_string, new_string) to update it."
			)

		# --- Build frontmatter + body ---
		content = (
			f"---\n"
			f"name: {name.strip()}\n"
			f"description: {description.strip()}\n"
			f"type: {type}\n"
			f"---\n"
			f"\n"
			f"{body.strip()}\n"
		)

		target.parent.mkdir(parents=True, exist_ok=True)
		target.write_text(content, encoding="utf-8")
		return json.dumps({
			"filename": filename,
			"bytes": len(content.encode("utf-8")),
			"type": type,
		})


	# ── Archive (Maintain only) ──────────────────────────────────────

	def archive(self, filename: str) -> str:
		"""Soft-delete a memory by moving it to the archived/ subdirectory.

		The file is not permanently deleted -- it can be restored by moving
		it back. Only .md files in the memory root can be archived.

		Args:
			filename: Memory filename to archive (e.g. "feedback_old.md").
		"""
		import json
		import shutil

		path = self._resolve(filename)
		if path is None:
			return "Error: no trace path configured"
		if not path.exists():
			return f"Error: file not found: {filename}"
		if path.suffix != ".md":
			return f"Error: only .md files can be archived, got: {filename}"
		if filename == "index.md":
			return "Error: cannot archive index.md"

		archive_dir = self.memory_root / "archived"
		archive_dir.mkdir(parents=True, exist_ok=True)
		target = archive_dir / path.name
		shutil.move(str(path), str(target))
		return json.dumps({"archived": filename, "moved_to": f"archived/{filename}"})

	# ── Edit ────────────────────────────────────────────────────────────

	def edit(self, filename: str, old_string: str, new_string: str,
	         near_line: int = 0) -> str:
		"""Replace old_string with new_string in a file. Surgical edit.

		Three-phase matching:
		1. Exact match on old_string.
		2. Fuzzy fallback: normalizes whitespace and retries.
		3. If multiple matches, near_line (1-based) picks the closest.

		Use read() first to see line numbers, then edit with near_line
		for disambiguation when needed.

		Args:
			filename: File to edit (e.g. "feedback_tabs.md" or "index.md").
			old_string: The exact text to find and replace.
			new_string: The replacement text.
			near_line: Hint for disambiguation when multiple matches exist.
				1-based line number. 0 means no hint. Default 0.
		"""
		path = self._resolve(filename)
		if path is None:
			return "Error: no trace path configured"
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


# ── Helpers for edit (module-level, not tools) ──────────────────────


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


if __name__ == "__main__":
	from pathlib import Path

	# Test with .local/dspy_optimization_guide.md as a "trace" file
	test_dir = Path(__file__).resolve().parents[3] / ".local"
	tools = MemoryTools(
		memory_root=test_dir,
		trace_path=test_dir / "dspy_optimization_guide.md",
	)

	print("=== Full read (small slice via limit) ===")
	print(tools.read("trace", limit=10))
	print()

	print("=== Paginated: offset=50, limit=5 ===")
	print(tools.read("trace", offset=50, limit=5))
	print()

	print("=== Read by filename (same file, as if it were a memory) ===")
	print(tools.read("dspy_optimization_guide.md", limit=5))
	print()

	print("=== Full read of small file (no limit) ===")
	# read first 3 lines to keep output short — but shows no header
	print(tools.read("dspy_optimization_guide.md", limit=3))
	print()

	print("=== Error: nonexistent file ===")
	print(tools.read("nonexistent.md"))
	print()

	print("=== Error: trace not configured ===")
	tools_no_trace = MemoryTools(memory_root=test_dir)
	print(tools_no_trace.read("trace"))

	print()
	print("=" * 60)
	print("GREP TESTS")
	print("=" * 60)

	print("\n=== Grep: find 'MIPROv2' in trace ===")
	print(tools.grep("trace", "MIPROv2"))
	print()

	print("=== Grep: no matches ===")
	print(tools.grep("trace", "xyznonexistent123"))
	print()

	print("=== Grep: by memory filename ===")
	print(tools.grep("dspy_optimization_guide.md", "BootstrapFewShot"))

	print()
	print("=" * 60)
	print("SCAN TESTS")
	print("=" * 60)

	# Use real lerim memory dir
	lerim_mem = Path(__file__).resolve().parents[3] / ".lerim" / "memory"
	if lerim_mem.is_dir():
		tools_mem = MemoryTools(memory_root=lerim_mem)

		print("\n=== Scan: memory root (rich manifest) ===")
		print(tools_mem.scan())
		print()

		print("=== Scan: summaries subdir ===")
		print(tools_mem.scan("summaries"))
		print()

		print("=== Scan: archived subdir ===")
		print(tools_mem.scan("archived"))
	else:
		print(f"\n(skipped — {lerim_mem} not found)")

	print()
	print("=== Scan: nonexistent directory ===")
	print(tools.scan("nonexistent_dir"))

	print()
	print("=" * 60)
	print("WRITE + EDIT TESTS")
	print("=" * 60)

	# Use a temp dir so we don't pollute real memory
	import tempfile, shutil, json
	with tempfile.TemporaryDirectory() as tmp:
		tmp_path = Path(tmp)
		tw = MemoryTools(memory_root=tmp_path)

		# --- Write: create new memory ---
		print("\n=== Write: create new feedback memory ===")
		result = tw.write(
			type="feedback",
			name="Use tabs not spaces",
			description="User prefers tabs for indentation",
			body="Always use tabs.\n\n**Why:** User preference.\n\n**How to apply:** All code files.",
		)
		print(result)

		# Read it back
		created_filename = json.loads(result)["filename"]
		print(f"\n=== Read back: {created_filename} ===")
		print(tw.read(created_filename))

		# --- Write: file already exists → error ---
		print("\n=== Write: same name again (should error) ===")
		print(tw.write(
			type="feedback",
			name="Use tabs not spaces",
			description="duplicate attempt",
			body="This should fail.",
		))

		# --- Write: summary type → summaries/ subdir ---
		print("\n=== Write: create summary ===")
		result_sum = tw.write(
			type="summary",
			name="DSPy migration session",
			description="Migrated from OAI SDK to DSPy ReAct",
			body="## User Intent\n\nMigrate the agent runtime.\n\n## What Happened\n\nReplaced OAI with DSPy.",
		)
		print(result_sum)
		sum_filename = json.loads(result_sum)["filename"]
		print(f"\n=== Read back summary: {sum_filename} ===")
		print(tw.read(sum_filename))

		# --- Write: create index.md manually via edit flow ---
		# First create it
		index_path = tmp_path / "index.md"
		index_body = (
			"# Test Project Memory\n\n"
			"## User Preferences\n"
			f"- [Use tabs]({created_filename}) — tabs for indentation\n\n"
			"## Project State\n"
			"- (none yet)\n"
		)
		index_path.write_text(index_body, encoding="utf-8")
		print("\n=== Read: index.md ===")
		print(tw.read("index.md"))

		# --- Edit: surgical update in index.md ---
		print("\n=== Edit: update index.md entry ===")
		print(tw.edit("index.md", "tabs for indentation", "tabs for all indentation"))
		print(tw.read("index.md"))

		# --- Write: validation errors ---
		print("\n=== Write: invalid type ===")
		print(tw.write(type="invalid", name="x", description="x", body="x"))
		print("\n=== Write: empty name ===")
		print(tw.write(type="user", name="", description="x", body="x"))

		# --- Edit: exact match in memory ---
		print("\n=== Edit: exact match in memory ===")
		print(tw.edit(created_filename, "All code files.", "All code files, no exceptions."))
		print(tw.read(created_filename))

		# --- Edit: not found ---
		print("\n=== Edit: old_string not found ===")
		print(tw.edit(created_filename, "this text does not exist", "replacement"))

		# --- Edit: multiple matches + near_line ---
		dup_file = tmp_path / "feedback_dup_test.md"
		dup_file.write_text("---\nname: dup\ndescription: test\ntype: feedback\n---\n\nFoo bar\nSome text\nFoo bar\nMore text\n")
		print("\n=== Edit: multiple matches, no near_line ===")
		print(tw.edit("feedback_dup_test.md", "Foo bar", "Replaced"))
		print("\n=== Edit: multiple matches, near_line=9 ===")
		dup_file.write_text("---\nname: dup\ndescription: test\ntype: feedback\n---\n\nFoo bar\nSome text\nFoo bar\nMore text\n")
		print(tw.edit("feedback_dup_test.md", "Foo bar", "Replaced second", near_line=9))
		print(tw.read("feedback_dup_test.md"))

		print()
		print("=" * 60)
		print("ARCHIVE TESTS")
		print("=" * 60)

		print("\n=== Archive: move memory to archived/ ===")
		print(tw.archive(created_filename))

		print("\n=== Scan: memory root after archive ===")
		print(tw.scan())

		print("\n=== Scan: archived/ subdir ===")
		print(tw.scan("archived"))

		print("\n=== Archive: file not found ===")
		print(tw.archive("nonexistent.md"))

		print("\n=== Archive: cannot archive index.md ===")
		print(tw.archive("index.md"))
