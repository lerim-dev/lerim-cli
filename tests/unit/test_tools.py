"""Unit tests for memory tool functions and history processors."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from lerim.agents.tools import (
	CONTEXT_HARD_PRESSURE_PCT,
	CONTEXT_SOFT_PRESSURE_PCT,
	ExtractDeps,
	Finding,
	MEMORY_TYPES,
	MODEL_CONTEXT_TOKEN_LIMIT,
	PRUNED_STUB,
	archive,
	build_test_ctx,
	compute_request_budget,
	context_pressure_injector,
	edit,
	grep,
	note,
	notes_state_injector,
	prune,
	prune_history_processor,
	read,
	scan,
	verify_index,
	write,
)


class ToolHarness:
	"""Tiny wrapper exposing method-style access over module-level tools."""

	def __init__(self, *, memory_root: Path, trace_path: Path | None = None):
		self._ctx = build_test_ctx(memory_root=memory_root, trace_path=trace_path)

	def read(self, filename: str, offset: int = 0, limit: int = 0) -> str:
		return read(self._ctx, filename=filename, offset=offset, limit=limit)

	def grep(self, filename: str, pattern: str, context_lines: int = 2) -> str:
		return grep(
			self._ctx,
			filename=filename,
			pattern=pattern,
			context_lines=context_lines,
		)

	def scan(self, directory: str = "", pattern: str = "*.md") -> str:
		return scan(self._ctx, directory=directory, pattern=pattern)

	def write(self, type: str, name: str, description: str, body: str) -> str:
		return write(self._ctx, type=type, name=name, description=description, body=body)

	def edit(
		self,
		filename: str,
		old_string: str,
		new_string: str,
		near_line: int = 0,
	) -> str:
		return edit(
			self._ctx,
			filename=filename,
			old_string=old_string,
			new_string=new_string,
			near_line=near_line,
		)

	def archive(self, filename: str) -> str:
		return archive(self._ctx, filename=filename)

	def verify_index(self, filename: str = "index.md") -> str:
		return verify_index(self._ctx, filename=filename)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mem_root(tmp_path):
	"""Create a memory root with sample files."""
	root = tmp_path / "memory"
	root.mkdir()
	return root


@pytest.fixture
def trace_file(tmp_path):
	"""Create a sample trace file (100 lines)."""
	trace = tmp_path / "trace.jsonl"
	lines = [f'{{"turn": {i}, "role": "user", "content": "message {i}"}}' for i in range(100)]
	trace.write_text("\n".join(lines), encoding="utf-8")
	return trace


@pytest.fixture
def tools(mem_root, trace_file):
	"""Tool harness instance with memory root and trace."""
	return ToolHarness(memory_root=mem_root, trace_path=trace_file)


@pytest.fixture
def tools_no_trace(mem_root):
	"""Tool harness instance without a trace path."""
	return ToolHarness(memory_root=mem_root)


def _write_memory_file(mem_root, filename, name, description, mem_type="feedback"):
	"""Write a minimal memory file for testing."""
	content = f"---\nname: {name}\ndescription: {description}\ntype: {mem_type}\n---\n\nBody of {name}.\n"
	path = mem_root / filename
	path.write_text(content, encoding="utf-8")
	return path


# ---------------------------------------------------------------------------
# read
# ---------------------------------------------------------------------------


class TestRead:
	def test_full_read_small_file(self, tools, mem_root):
		"""Full read (limit=0) returns entire file with line numbers, no header."""
		_write_memory_file(mem_root, "feedback_tabs.md", "Use tabs", "Tabs over spaces")
		result = tools.read("feedback_tabs.md")
		assert "1\t---" in result
		assert "Body of Use tabs" in result
		# No header for full reads
		assert "[" not in result.split("\n")[0] or "lines" not in result.split("\n")[0]

	def test_paginated_read_trace(self, tools):
		"""Paginated read returns header + correct line range."""
		result = tools.read("trace", offset=10, limit=5)
		assert "[100 lines, showing 11-15]" in result
		lines = result.strip().split("\n")
		assert lines[1].startswith("11\t")
		assert lines[5].startswith("15\t")

	def test_full_trace_read(self, tools):
		"""limit=0 on trace reads entire file."""
		result = tools.read("trace")
		assert "1\t" in result
		assert "100\t" in result

	def test_read_index_md(self, tools, mem_root):
		"""Can read index.md by filename."""
		(mem_root / "index.md").write_text("# Project Memory\n\n## Preferences\n- entry\n")
		result = tools.read("index.md")
		assert "Project Memory" in result

	def test_read_nonexistent(self, tools):
		"""Missing file returns error."""
		result = tools.read("nonexistent.md")
		assert "Error" in result
		assert "not found" in result

	def test_read_trace_not_configured(self, tools_no_trace):
		"""Reading trace without trace_path returns error."""
		result = tools_no_trace.read("trace")
		assert "Error" in result
		assert "no trace path" in result

	def test_offset_beyond_file(self, tools):
		"""Offset past end of file returns header but no content lines."""
		result = tools.read("trace", offset=200, limit=10)
		assert "[100 lines, showing 201-200]" in result

	def test_read_empty_file(self, tools, mem_root):
		"""Read a 0-byte .md file returns empty string (no crash)."""
		empty = mem_root / "feedback_empty.md"
		empty.write_text("", encoding="utf-8")
		result = tools.read("feedback_empty.md")
		assert isinstance(result, str)
		# Empty file has no lines, so full read produces empty output
		assert result == ""

	def test_read_negative_limit(self, tools):
		"""limit=-1 on trace is capped at TRACE_MAX_LINES_PER_READ (100).

		Trace reads are always chunked to keep the agent's trajectory bounded.
		Negative or zero limits get replaced with the hard cap.
		"""
		from lerim.agents.tools import TRACE_MAX_LINES_PER_READ

		result = tools.read("trace", limit=-1)
		assert "1\t" in result
		# Trace reads always include the pagination header now.
		assert f"[100 lines, showing 1-{TRACE_MAX_LINES_PER_READ}]" in result

	# -----------------------------------------------------------------
	# Byte cap regression tests (Bug #3: context window exceeded)
	# -----------------------------------------------------------------
	#
	# Regression for "context window exceeds limit" failures on 2/30 cases
	# in the 2026-04-11 baseline. Raw Claude trace events can be 50KB+ per
	# line (large tool results), so a 100-line chunk was blowing MiniMax's
	# input limit by turn 3. Fix: per-line cap at TRACE_MAX_LINE_BYTES,
	# total chunk cap at TRACE_MAX_CHUNK_BYTES. Memory-file reads untouched.

	def test_trace_huge_single_line_is_truncated(self, tmp_path):
		"""A single trace line >TRACE_MAX_LINE_BYTES gets truncated in place."""
		from lerim.agents.tools import TRACE_MAX_LINE_BYTES

		mem = tmp_path / "memory"
		mem.mkdir()
		trace = tmp_path / "trace.jsonl"
		# Line 0: 20KB of 'x'. Line 1: normal short line.
		big_line = "x" * 20_000
		trace.write_text(big_line + "\n" + "short line 2\n", encoding="utf-8")

		tools = ToolHarness(memory_root=mem, trace_path=trace)
		result = tools.read("trace", offset=0, limit=2)

		# The big line is truncated with a marker; the short line survives.
		# Header says there are 2 total lines.
		assert "[2 lines," in result
		assert "truncated" in result
		assert "chars from this line" in result
		# The returned big line (after its line-number prefix) must be
		# no larger than TRACE_MAX_LINE_BYTES + a small marker suffix.
		# Split off the header line, take the first content line.
		content = result.split("\n", 1)[1]
		first_line = content.split("\n")[0]  # "1\t<truncated content>"
		# Strip line-number prefix "1\t"
		line_body = first_line.split("\t", 1)[1]
		# Body length <= cap + marker (~60 chars)
		assert len(line_body) <= TRACE_MAX_LINE_BYTES + 100, (
			f"Line body is {len(line_body)} chars, should be <= {TRACE_MAX_LINE_BYTES + 100}"
		)
		# Short line 2 should still be visible
		assert "short line 2" in result

	def test_trace_chunk_byte_cap_cuts_chunk_short(self, tmp_path):
		"""A 100-line chunk whose total bytes >TRACE_MAX_CHUNK_BYTES is cut short."""
		from lerim.agents.tools import (
			TRACE_MAX_CHUNK_BYTES,
			TRACE_MAX_LINES_PER_READ,
		)

		mem = tmp_path / "memory"
		mem.mkdir()
		trace = tmp_path / "trace.jsonl"
		# 100 lines × 2KB each = 200KB total, well over the 50KB chunk cap.
		lines = ["y" * 2_000 for _ in range(100)]
		trace.write_text("\n".join(lines) + "\n", encoding="utf-8")

		tools = ToolHarness(memory_root=mem, trace_path=trace)
		result = tools.read("trace", offset=0, limit=100)

		# Count how many lines the cap let through (line-numbered content rows).
		content_rows = [
			row for row in result.split("\n")
			if "\t" in row and row.split("\t", 1)[0].isdigit()
		]
		# Each line is ~2000 bytes. 50KB / 2000 ≈ 25 lines. Assert the cap
		# triggered (i.e. we got fewer than 100 lines) AND the rough shape
		# is right.
		assert len(content_rows) < TRACE_MAX_LINES_PER_READ, (
			f"Expected cap to cut chunk short; got {len(content_rows)} lines"
		)
		# Total bytes of content rows should be <= cap + small overhead
		# (line numbers, tabs, newlines).
		content_bytes = sum(len(row.encode("utf-8")) for row in content_rows)
		assert content_bytes <= TRACE_MAX_CHUNK_BYTES + 1_000, (
			f"Content bytes {content_bytes} exceeded chunk cap {TRACE_MAX_CHUNK_BYTES}"
		)
		# Header should announce that more lines remain (pagination works).
		assert "more lines" in result
		assert "offset=" in result

	def test_trace_small_lines_unchanged_by_cap(self, tools):
		"""Normal-sized trace lines (tools fixture) are unaffected by byte caps."""
		result = tools.read("trace", offset=0, limit=50)
		# The fixture has 100 lines of short JSON; 50-line chunk is ~3KB,
		# well under both caps. All 50 lines should come back.
		content_rows = [
			row for row in result.split("\n")
			if "\t" in row and row.split("\t", 1)[0].isdigit()
		]
		assert len(content_rows) == 50
		assert "[100 lines, showing 1-50]" in result
		assert "truncated" not in result

	def test_memory_file_read_ignores_byte_cap(self, tools, mem_root):
		"""Memory-file reads are NOT subject to trace byte caps (small files only)."""
		# Intentionally write a memory file larger than TRACE_MAX_LINE_BYTES
		# to prove the cap doesn't apply to non-trace reads.
		huge_body = "z" * 10_000
		(mem_root / "feedback_huge.md").write_text(
			f"---\nname: Huge\ndescription: Big body\ntype: feedback\n---\n\n{huge_body}\n",
			encoding="utf-8",
		)
		result = tools.read("feedback_huge.md")
		# All 10k chars of z must come back intact — no truncation for memory files.
		assert "z" * 10_000 in result
		assert "truncated" not in result


# ---------------------------------------------------------------------------
# grep
# ---------------------------------------------------------------------------


class TestGrep:
	def test_grep_finds_matches(self, tools):
		"""Grep returns matching lines from trace."""
		result = tools.grep("trace", "message 42")
		assert "42" in result
		assert "message 42" in result

	def test_grep_no_matches(self, tools):
		"""Grep returns clear message when nothing matches."""
		result = tools.grep("trace", "xyznonexistent")
		assert "No matches" in result

	def test_grep_memory_file(self, tools, mem_root):
		"""Grep works on memory files too."""
		_write_memory_file(mem_root, "feedback_tabs.md", "Use tabs", "Tabs pref")
		result = tools.grep("feedback_tabs.md", "Body of")
		assert "Body of Use tabs" in result

	def test_grep_nonexistent_file(self, tools):
		"""Grep on missing file returns error."""
		result = tools.grep("nonexistent.md", "pattern")
		assert "Error" in result
		assert "not found" in result

	def test_grep_trace_not_configured(self, tools_no_trace):
		"""Grep on trace without trace_path returns error."""
		result = tools_no_trace.grep("trace", "pattern")
		assert "Error" in result
		assert "no trace path" in result


# ---------------------------------------------------------------------------
# scan
# ---------------------------------------------------------------------------


class TestScan:
	def test_scan_empty_root(self, tools):
		"""Scan on empty memory root returns count 0."""
		result = json.loads(tools.scan())
		assert result["count"] == 0
		assert result["memories"] == []

	def test_scan_with_memories(self, tools, mem_root):
		"""Scan returns manifest with filename, description, modified."""
		_write_memory_file(mem_root, "feedback_tabs.md", "Use tabs", "Tabs pref", "feedback")
		_write_memory_file(mem_root, "project_arch.md", "Architecture", "framework arch", "project")
		result = json.loads(tools.scan())
		assert result["count"] == 2
		filenames = {m["filename"] for m in result["memories"]}
		assert "feedback_tabs.md" in filenames
		assert "project_arch.md" in filenames
		assert "description" in result["memories"][0]
		assert "modified" in result["memories"][0]

	def test_scan_excludes_index(self, tools, mem_root):
		"""Scan excludes index.md from the manifest."""
		_write_memory_file(mem_root, "feedback_tabs.md", "Use tabs", "Tabs pref")
		(mem_root / "index.md").write_text("# Index\n")
		result = json.loads(tools.scan())
		assert result["count"] == 1
		filenames = {m["filename"] for m in result["memories"]}
		assert "index.md" not in filenames

	def test_scan_subdirectory(self, tools, mem_root):
		"""Scan subdirectory returns file listing with modified time."""
		archived = mem_root / "archived"
		archived.mkdir()
		(archived / "old.md").write_text("old content")
		result = json.loads(tools.scan("archived"))
		assert result["count"] == 1
		assert result["files"][0]["filename"] == "old.md"
		assert "modified" in result["files"][0]

	def test_scan_nonexistent_dir(self, tools):
		"""Scan nonexistent directory returns empty result."""
		result = json.loads(tools.scan("nonexistent"))
		assert result["count"] == 0

	def test_scan_summaries(self, tools, mem_root):
		"""Scan summaries subdirectory works."""
		summaries = mem_root / "summaries"
		summaries.mkdir()
		(summaries / "summary_test.md").write_text("summary content")
		result = json.loads(tools.scan("summaries"))
		assert result["count"] == 1

	def test_scan_write_roundtrip(self, tools, mem_root):
		"""write() then immediately scan() -> new file appears in manifest."""
		# Start empty
		before = json.loads(tools.scan())
		assert before["count"] == 0

		# Write a memory
		tools.write(
			type="feedback", name="Roundtrip test",
			description="Testing scan after write",
			body="Use tabs for indentation.\n**Why:** Team convention.\n**How to apply:** Set indent_style=tab.",
		)
		after = json.loads(tools.scan())
		assert after["count"] == 1
		assert after["memories"][0]["filename"] == "feedback_roundtrip_test.md"
		assert after["memories"][0]["description"] == "Testing scan after write"

	def test_scan_archive_roundtrip(self, tools, mem_root):
		"""write() then archive() then scan() -> file gone from manifest, appears in archived."""
		tools.write(
			type="feedback", name="Archive roundtrip",
			description="Will be archived",
			body="Archive this.\n**Why:** Testing archival.\n**How to apply:** Check archived dir.",
		)
		# Verify it exists in main scan
		before = json.loads(tools.scan())
		assert before["count"] == 1

		# Archive it
		tools.archive("feedback_archive_roundtrip.md")

		# Gone from main manifest
		after = json.loads(tools.scan())
		assert after["count"] == 0

		# Present in archived subdirectory
		archived = json.loads(tools.scan("archived"))
		assert archived["count"] == 1
		assert archived["files"][0]["filename"] == "feedback_archive_roundtrip.md"


# ---------------------------------------------------------------------------
# write
# ---------------------------------------------------------------------------


class TestWrite:
	def test_create_feedback(self, tools, mem_root):
		"""Write creates a new feedback memory file."""
		result = json.loads(tools.write(
			type="feedback", name="Use tabs",
			description="Tabs over spaces",
			body="Always use tabs.\n**Why:** Team convention.\n**How to apply:** Set indent_style=tab.",
		))
		assert result["type"] == "feedback"
		assert result["filename"] == "feedback_use_tabs.md"
		path = mem_root / result["filename"]
		assert path.exists()
		content = path.read_text()
		assert "name: Use tabs" in content
		assert "type: feedback" in content
		assert "Always use tabs." in content

	def test_create_project(self, tools, mem_root):
		"""Write creates a project memory."""
		result = json.loads(tools.write(
			type="project", name="framework migration",
			description="Migrated to PydanticAI agent",
			body="Migration completed.\n\n**Why:** Optimizable.\n\n**How to apply:** Use LerimRuntime.",
		))
		assert result["type"] == "project"
		assert (mem_root / result["filename"]).exists()

	def test_create_summary(self, tools, mem_root):
		"""Write with type=summary creates file in summaries/ subdir."""
		result = json.loads(tools.write(
			type="summary", name="Migration session",
			description="Migrated to PydanticAI",
			body="## User Intent\n\nMigrate runtime.\n\n## What Happened\n\nDone.",
		))
		assert result["type"] == "summary"
		# filename now includes summaries/ prefix for correct index.md links
		assert result["filename"].startswith("summaries/")
		assert (mem_root / result["filename"]).exists()

	def test_no_timestamps_in_frontmatter(self, tools, mem_root):
		"""Frontmatter should only have name, description, type — no timestamps."""
		result = json.loads(tools.write(
			type="user", name="Isaac",
			description="Founder context", body="ML/AI PhD.",
		))
		content = (mem_root / result["filename"]).read_text()
		assert "created:" not in content
		assert "updated:" not in content
		assert "name: Isaac" in content
		assert "description: Founder context" in content
		assert "type: user" in content

	def test_file_exists_returns_error(self, tools, mem_root):
		"""Write same name twice returns error pointing to read + edit."""
		tools.write(type="feedback", name="Use tabs",
		            description="Tabs pref",
		            body="Use tabs.\n**Why:** Convention.\n**How to apply:** Set tabs.")
		result = tools.write(type="feedback", name="Use tabs",
		                     description="Dup attempt",
		                     body="Use tabs.\n**Why:** Convention.\n**How to apply:** Set tabs.")
		assert "Error" in result
		assert "already exists" in result
		assert "read(" in result
		assert "edit(" in result

	def test_invalid_type(self, tools):
		"""Invalid type returns error listing valid types."""
		result = tools.write(type="invalid", name="x", description="x", body="x")
		assert "Error" in result
		assert "user" in result

	def test_empty_name(self, tools):
		"""Empty name returns error."""
		result = tools.write(type="feedback", name="", description="x", body="x")
		assert "Error" in result
		assert "name" in result

	def test_empty_description(self, tools):
		"""Empty description returns error."""
		result = tools.write(type="feedback", name="x", description="", body="x")
		assert "Error" in result
		assert "description" in result

	def test_empty_body(self, tools):
		"""Empty body returns error."""
		result = tools.write(type="feedback", name="x", description="x", body="")
		assert "Error" in result
		assert "body" in result

	def test_all_memory_types(self, tools, mem_root):
		"""All MEMORY_TYPES can be created."""
		for t in MEMORY_TYPES:
			if t in ("feedback", "project"):
				body = f"Rule for {t}.\n**Why:** Reason.\n**How to apply:** Action."
			elif t == "summary":
				body = f"## User Intent\n\nTest {t}.\n\n## What Happened\n\nDone."
			else:
				body = f"Body for {t}."
			result = json.loads(tools.write(
				type=t, name=f"Test {t}",
				description=f"Desc for {t}", body=body,
			))
			assert result["type"] == t

	def test_write_slug_sanitization(self, tools, mem_root):
		"""Name with special chars -> filename contains only alphanumeric + underscores."""
		result = json.loads(tools.write(
			type="feedback", name="Use (tabs) & spaces!!",
			description="Sanitization test",
			body="Use tabs.\n**Why:** Convention.\n**How to apply:** Set tabs.",
		))
		filename = result["filename"]
		# Strip the "feedback_" prefix and ".md" suffix to get the slug
		slug = filename[len("feedback_"):-len(".md")]
		# Slug should only contain lowercase alphanumeric and underscores
		assert re.fullmatch(r"[a-z0-9_]+", slug), f"Slug contains invalid chars: {slug}"
		# Parentheses, ampersand, exclamation marks should all be gone
		assert "(" not in slug
		assert "&" not in slug
		assert "!" not in slug
		assert (mem_root / filename).exists()

	def test_write_duplicate_name_different_type(self, tools, mem_root):
		"""Write feedback 'Auth' then project 'Auth' -> both files created (different type prefix)."""
		r1 = json.loads(tools.write(
			type="feedback", name="Auth",
			description="Auth feedback",
			body="Use OAuth2.\n**Why:** Security.\n**How to apply:** Add OAuth2 middleware.",
		))
		r2 = json.loads(tools.write(
			type="project", name="Auth",
			description="Auth project",
			body="OAuth2 chosen.\n**Why:** Compliance.\n**How to apply:** Migrate all endpoints.",
		))
		assert r1["filename"] == "feedback_auth.md"
		assert r2["filename"] == "project_auth.md"
		assert r1["filename"] != r2["filename"]
		assert (mem_root / r1["filename"]).exists()
		assert (mem_root / r2["filename"]).exists()

	def test_write_body_with_frontmatter_delimiter(self, tools, mem_root):
		"""Body containing '---' on its own line -> file is still valid, frontmatter intact."""
		body_with_delimiters = "Some text.\n**Why:** Reason.\n**How to apply:** Action.\n\n---\n\nMore text after horizontal rule."
		result = json.loads(tools.write(
			type="feedback", name="Delimiter test",
			description="Body has triple dashes", body=body_with_delimiters,
		))
		filename = result["filename"]
		path = mem_root / filename
		assert path.exists()

		# Verify the file can be re-read and frontmatter parses correctly
		import frontmatter as fm_lib
		post = fm_lib.load(str(path))
		assert post.get("name") == "Delimiter test"
		assert post.get("description") == "Body has triple dashes"
		assert post.get("type") == "feedback"
		# Body content should contain the --- separator
		assert "---" in post.content

	def test_write_very_long_name(self, tools, mem_root):
		"""Name with 200+ chars -> slug truncated to 128 chars, file created."""
		long_name = "a" * 210
		result = json.loads(tools.write(
			type="feedback", name=long_name,
			description="Long name test",
			body="Rule.\n**Why:** Reason.\n**How to apply:** Action.",
		))
		filename = result["filename"]
		slug = filename[len("feedback_"):-len(".md")]
		assert len(slug) <= 128
		# The full filename is type_ + slug + .md
		assert (mem_root / filename).exists()
		# Read it back to confirm it works
		content = tools.read(filename)
		assert "Rule." in content

	# -----------------------------------------------------------------
	# YAML frontmatter stress tests (Bug #1: colon-in-description crash)
	# -----------------------------------------------------------------
	#
	# Regression for the "mapping values are not allowed in this context"
	# class of failures observed on 9/30 cases in the 2026-04-11 baseline.
	# The old implementation built frontmatter via f-string concat, so any
	# YAML special character (':', '#', '"', '[', leading '-', '|', '>')
	# inside `name` or `description` corrupted the file. The fix routes
	# serialization through yaml.safe_dump which auto-quotes such values.

	@pytest.mark.parametrize(
		"value",
		[
			"Redis chosen: faster than Memcached",    # the literal bug trigger
			'Quote in "description" field',           # embedded double quotes
			"leading: colon and: more: colons",       # many colons
			"ends with a colon:",
			"#hashtag at start",                       # YAML comment marker
			"- leading dash",                          # list item marker
			"[flow sequence]",                         # flow style
			"key: value | pipe >> gt",                 # block scalar markers
			"Unicode: café — résumé ☃",              # unicode
			"Multi\nline\nvalue",                      # embedded newlines
			"value with 'single quotes' and \"double\"",
			"'starts with quote",
		],
	)
	def test_write_yaml_roundtrip_description(self, tools, mem_root, value):
		"""Every YAML special char in description: write -> frontmatter.load round-trip."""
		import frontmatter as fm_lib

		result = json.loads(tools.write(
			type="feedback",
			name="roundtrip",
			description=value,
			body="Rule here.\n**Why:** Reason.\n**How to apply:** Action.",
		))
		path = mem_root / result["filename"]
		assert path.exists()

		# The real bug: frontmatter.load used to crash with ScannerError.
		# After the yaml.safe_dump fix it must round-trip losslessly.
		post = fm_lib.load(str(path))
		assert post.get("description") == value, (
			f"YAML round-trip failed for description={value!r}; "
			f"got {post.get('description')!r}"
		)
		assert post.get("name") == "roundtrip"
		assert post.get("type") == "feedback"
		assert "Rule here." in post.content

	@pytest.mark.parametrize(
		"value",
		[
			"Name: with colon",
			"Name #with hash",
			"Name with - dash",
			'Name "with" quotes',
			"Name | with | pipes",
		],
	)
	def test_write_yaml_roundtrip_name(self, tools, mem_root, value):
		"""Every YAML special char in name: write -> frontmatter.load round-trip."""
		import frontmatter as fm_lib

		result = json.loads(tools.write(
			type="feedback",
			name=value,
			description="stress test",
			body="Rule.\n**Why:** Reason.\n**How to apply:** Action.",
		))
		path = mem_root / result["filename"]
		assert path.exists()

		post = fm_lib.load(str(path))
		assert post.get("name") == value
		assert post.get("description") == "stress test"
		assert post.get("type") == "feedback"

	def test_write_yaml_roundtrip_extreme_combined(self, tools, mem_root):
		"""Pathological combined case: every nasty char in both name and description."""
		import frontmatter as fm_lib

		nasty_name = 'Name: with #all [the] - "YAML" | specials'
		nasty_description = (
			"Redis chosen: because Memcached can't scale #reasons. "
			'Quote: "faster by 10x". Leading dash: - stuff.'
		)
		result = json.loads(tools.write(
			type="project",
			name=nasty_name,
			description=nasty_description,
			body="Redis chosen as cache.\n**Why:** Needs pub/sub.\n**How to apply:** Use Redis for new caching.",
		))
		path = mem_root / result["filename"]
		assert path.exists()

		post = fm_lib.load(str(path))
		assert post.get("name") == nasty_name
		assert post.get("description") == nasty_description
		assert post.get("type") == "project"


# ---------------------------------------------------------------------------
# edit
# ---------------------------------------------------------------------------


class TestEdit:
	def test_exact_match(self, tools, mem_root):
		"""Edit replaces exact match in a memory file."""
		_write_memory_file(mem_root, "feedback_tabs.md", "Use tabs", "Tabs pref")
		result = tools.edit("feedback_tabs.md", "Body of Use tabs.", "Updated body.")
		assert "Edited" in result
		content = (mem_root / "feedback_tabs.md").read_text()
		assert "Updated body." in content
		assert "Body of Use tabs" not in content

	def test_edit_index(self, tools, mem_root):
		"""Edit works on index.md for surgical updates."""
		(mem_root / "index.md").write_text(
			"# Memory\n\n## Preferences\n- [Tabs](feedback_tabs.md) — tabs pref\n"
		)
		result = tools.edit("index.md", "tabs pref", "tabs for all indentation")
		assert "Edited" in result
		content = (mem_root / "index.md").read_text()
		assert "tabs for all indentation" in content

	def test_not_found_string(self, tools, mem_root):
		"""Edit returns error when old_string not found."""
		_write_memory_file(mem_root, "feedback_tabs.md", "Use tabs", "Tabs pref")
		result = tools.edit("feedback_tabs.md", "nonexistent text", "replacement")
		assert "Error" in result
		assert "not found" in result

	def test_not_found_file(self, tools):
		"""Edit returns error when file doesn't exist."""
		result = tools.edit("nonexistent.md", "old", "new")
		assert "Error" in result
		assert "not found" in result

	def test_multiple_matches_no_hint(self, tools, mem_root):
		"""Edit with multiple matches and no near_line asks for disambiguation."""
		(mem_root / "test.md").write_text("AAA\nBBB\nAAA\nCCC\n")
		result = tools.edit("test.md", "AAA", "ZZZ")
		assert "Error" in result
		assert "matches 2 locations" in result
		assert "near_line" in result

	def test_multiple_matches_with_near_line(self, tools, mem_root):
		"""Edit with near_line picks the closest match."""
		(mem_root / "test.md").write_text("AAA\nBBB\nAAA\nCCC\n")
		result = tools.edit("test.md", "AAA", "ZZZ", near_line=3)
		assert "Edited" in result
		content = (mem_root / "test.md").read_text()
		lines = content.strip().split("\n")
		assert lines[0] == "AAA"  # First occurrence untouched
		assert lines[2] == "ZZZ"  # Second occurrence (line 3) replaced

	def test_fuzzy_whitespace_match(self, tools, mem_root):
		"""Edit falls back to fuzzy whitespace matching."""
		(mem_root / "test.md").write_text("    indented line\n")
		# Search with tabs instead of spaces
		result = tools.edit("test.md", "\tindented line", "fixed line")
		assert "Edited" in result

	def test_edit_preserves_frontmatter(self, tools, mem_root):
		"""Edit only body content, verify YAML frontmatter unchanged."""
		_write_memory_file(mem_root, "feedback_preserve.md", "Preserve FM", "Keep frontmatter intact")
		# Edit only the body
		result = tools.edit("feedback_preserve.md", "Body of Preserve FM.", "New body content.")
		assert "Edited" in result
		content = (mem_root / "feedback_preserve.md").read_text(encoding="utf-8")
		# Frontmatter should be untouched
		assert "name: Preserve FM" in content
		assert "description: Keep frontmatter intact" in content
		assert "type: feedback" in content
		# Body should be updated
		assert "New body content." in content
		assert "Body of Preserve FM" not in content

	def test_edit_with_regex_special_chars(self, tools, mem_root):
		"""old_string 'foo[bar]' (regex special) -> found as literal match, not regex."""
		(mem_root / "test_regex.md").write_text("Some foo[bar] text here.\n")
		result = tools.edit("test_regex.md", "foo[bar]", "replaced")
		assert "Edited" in result
		content = (mem_root / "test_regex.md").read_text(encoding="utf-8")
		assert "replaced" in content
		assert "foo[bar]" not in content

	def test_edit_empty_old_string(self, tools, mem_root):
		"""old_string='' -> matches at every position, returns disambiguation error."""
		(mem_root / "test_empty.md").write_text("Some content here.\n")
		result = tools.edit("test_empty.md", "", "replacement")
		# Empty string matches at every character position -> multiple matches error
		assert "Error" in result
		assert "matches" in result
		# File should be unchanged
		content = (mem_root / "test_empty.md").read_text(encoding="utf-8")
		assert content == "Some content here.\n"


# ---------------------------------------------------------------------------
# archive
# ---------------------------------------------------------------------------


class TestArchive:
	def test_archive_moves_file(self, tools, mem_root):
		"""Archive moves file to archived/ subdirectory."""
		_write_memory_file(mem_root, "feedback_old.md", "Old", "Old pref")
		result = json.loads(tools.archive("feedback_old.md"))
		assert result["archived"] == "feedback_old.md"
		assert not (mem_root / "feedback_old.md").exists()
		assert (mem_root / "archived" / "feedback_old.md").exists()

	def test_archive_then_scan(self, tools, mem_root):
		"""After archiving, file disappears from scan and appears in archived."""
		_write_memory_file(mem_root, "feedback_old.md", "Old", "Old pref")
		_write_memory_file(mem_root, "feedback_keep.md", "Keep", "Keep pref")
		tools.archive("feedback_old.md")

		manifest = json.loads(tools.scan())
		assert manifest["count"] == 1
		assert manifest["memories"][0]["filename"] == "feedback_keep.md"

		archived = json.loads(tools.scan("archived"))
		assert archived["count"] == 1
		assert archived["files"][0]["filename"] == "feedback_old.md"

	def test_archive_nonexistent(self, tools):
		"""Archive nonexistent file returns error."""
		result = tools.archive("nonexistent.md")
		assert "Error" in result
		assert "not found" in result

	def test_archive_index_protected(self, tools, mem_root):
		"""Cannot archive index.md."""
		(mem_root / "index.md").write_text("# Index\n")
		result = tools.archive("index.md")
		assert "Error" in result
		assert "cannot archive" in result

	def test_archive_creates_archived_dir(self, tools, mem_root):
		"""Archive when archived/ doesn't exist -> dir created, file moved."""
		archived_dir = mem_root / "archived"
		assert not archived_dir.exists()

		_write_memory_file(mem_root, "feedback_autodir.md", "Autodir", "Test dir creation")
		result = json.loads(tools.archive("feedback_autodir.md"))

		assert result["archived"] == "feedback_autodir.md"
		assert archived_dir.exists()
		assert archived_dir.is_dir()
		assert (archived_dir / "feedback_autodir.md").exists()
		assert not (mem_root / "feedback_autodir.md").exists()

	def test_archive_non_md_file(self, tools, mem_root):
		"""Try to archive 'data.json' -> error message about .md only."""
		json_file = mem_root / "data.json"
		json_file.write_text('{"key": "value"}', encoding="utf-8")

		result = tools.archive("data.json")
		assert "Error" in result
		assert ".md" in result


# ---------------------------------------------------------------------------
# verify_index
# ---------------------------------------------------------------------------


class TestVerifyIndex:
	def test_ok_when_consistent(self, tools, mem_root):
		"""Returns OK when index.md matches all memory files."""
		_write_memory_file(mem_root, "feedback_tabs.md", "Use tabs", "Tabs pref")
		_write_memory_file(mem_root, "project_arch.md", "Architecture", "framework arch")
		(mem_root / "index.md").write_text(
			"# Memory\n\n- [Tabs](feedback_tabs.md) — pref\n- [Arch](project_arch.md) — arch\n"
		)
		result = tools.verify_index()
		assert result.startswith("OK")
		assert "2 files" in result

	def test_missing_from_index(self, tools, mem_root):
		"""Reports files missing from index."""
		_write_memory_file(mem_root, "feedback_tabs.md", "Use tabs", "Tabs pref")
		_write_memory_file(mem_root, "project_arch.md", "Architecture", "framework arch")
		(mem_root / "index.md").write_text(
			"# Memory\n\n- [Tabs](feedback_tabs.md) — pref\n"
		)
		result = tools.verify_index()
		assert "NOT OK" in result
		assert "project_arch.md" in result
		assert "Missing from index" in result

	def test_stale_in_index(self, tools, mem_root):
		"""Reports index entries pointing to nonexistent files."""
		_write_memory_file(mem_root, "feedback_tabs.md", "Use tabs", "Tabs pref")
		(mem_root / "index.md").write_text(
			"# Memory\n\n- [Tabs](feedback_tabs.md) — pref\n- [Old](feedback_old.md) — gone\n"
		)
		result = tools.verify_index()
		assert "NOT OK" in result
		assert "feedback_old.md" in result
		assert "Stale" in result

	def test_no_index_file(self, tools, mem_root):
		"""Reports missing entries when index.md doesn't exist."""
		_write_memory_file(mem_root, "feedback_tabs.md", "Use tabs", "Tabs pref")
		result = tools.verify_index()
		assert "NOT OK" in result
		assert "feedback_tabs.md" in result

	def test_empty_memory_root(self, tools):
		"""Returns OK when both memory root and index are empty."""
		result = tools.verify_index()
		assert result.startswith("OK")

	def test_includes_description_for_missing(self, tools, mem_root):
		"""Missing entries include the file's description to help the agent."""
		_write_memory_file(mem_root, "feedback_tabs.md", "Use tabs", "Prefer tabs over spaces")
		(mem_root / "index.md").write_text("# Memory\n")
		result = tools.verify_index()
		assert "Prefer tabs over spaces" in result

	def test_verify_index_with_extra_sections(self, tools, mem_root):
		"""Index with multiple ## sections, some empty -> still validates correctly."""
		_write_memory_file(mem_root, "feedback_tabs.md", "Use tabs", "Tabs pref")
		_write_memory_file(mem_root, "project_arch.md", "Architecture", "framework arch")
		(mem_root / "index.md").write_text(
			"# Memory\n\n"
			"## User Preferences\n"
			"- [Tabs](feedback_tabs.md) — pref\n\n"
			"## Project State\n"
			"- [Arch](project_arch.md) — arch\n\n"
			"## References\n\n"
			"## Empty Section\n\n"
		)
		result = tools.verify_index()
		assert result.startswith("OK")
		assert "2 files" in result

	def test_verify_index_ignores_non_link_lines(self, tools, mem_root):
		"""Index with plain text lines (no markdown links) -> only link entries checked."""
		_write_memory_file(mem_root, "feedback_tabs.md", "Use tabs", "Tabs pref")
		(mem_root / "index.md").write_text(
			"# Memory\n\n"
			"This is a plain text description of the memory index.\n"
			"Some notes about the project go here.\n\n"
			"## User Preferences\n"
			"These are user preferences collected over time.\n"
			"- [Tabs](feedback_tabs.md) — pref\n\n"
			"## Notes\n"
			"Remember to update this regularly.\n"
		)
		result = tools.verify_index()
		# Only the markdown link entry counts, plain text lines are ignored
		assert result.startswith("OK")
		assert "1 files" in result or "1 entries" in result

	def test_duplicate_entry_detected(self, tools, mem_root):
		"""Reports when the same filename appears twice in index.md."""
		_write_memory_file(mem_root, "feedback_tabs.md", "Use tabs", "Tabs pref")
		_write_memory_file(mem_root, "project_arch.md", "Architecture", "framework arch")
		(mem_root / "index.md").write_text(
			"# Memory\n\n"
			"## User Preferences\n"
			"- [Tabs](feedback_tabs.md) — pref\n\n"
			"## Project State\n"
			"- [Arch](project_arch.md) — arch\n"
			"- [Arch duplicate](project_arch.md) — listed again\n"
		)
		result = tools.verify_index()
		assert "NOT OK" in result
		assert "Duplicate entry in index: project_arch.md" in result

	def test_duplicate_only_issue(self, tools, mem_root):
		"""Duplicate is the only issue -> still NOT OK."""
		_write_memory_file(mem_root, "feedback_tabs.md", "Use tabs", "Tabs pref")
		(mem_root / "index.md").write_text(
			"# Memory\n\n"
			"- [Tabs](feedback_tabs.md) — pref\n"
			"- [Tabs again](feedback_tabs.md) — duplicate\n"
		)
		result = tools.verify_index()
		assert "NOT OK" in result
		assert "Duplicate entry in index: feedback_tabs.md" in result
		# No missing or stale issues
		assert "Missing from index" not in result
		assert "Stale" not in result

	def test_no_duplicate_when_unique(self, tools, mem_root):
		"""No duplicate report when each file appears exactly once."""
		_write_memory_file(mem_root, "feedback_tabs.md", "Use tabs", "Tabs pref")
		_write_memory_file(mem_root, "project_arch.md", "Architecture", "framework arch")
		(mem_root / "index.md").write_text(
			"# Memory\n\n"
			"- [Tabs](feedback_tabs.md) — pref\n"
			"- [Arch](project_arch.md) — arch\n"
		)
		result = tools.verify_index()
		assert result.startswith("OK")
		assert "Duplicate" not in result


# ---------------------------------------------------------------------------
# Tool introspection
# ---------------------------------------------------------------------------


class TestToolIntrospection:
	def test_tools_are_callable_methods(self, tools):
		"""All tool methods are callable bound methods."""
		for method in [tools.read, tools.grep, tools.scan, tools.write, tools.edit, tools.archive, tools.verify_index]:
			assert callable(method)

	def test_tool_selection_per_agent(self, tools):
		"""Each agent gets the correct subset of tools."""
		extract = [tools.read, tools.grep, tools.scan, tools.write, tools.edit, tools.verify_index]
		maintain = [tools.read, tools.scan, tools.write, tools.edit, tools.archive, tools.verify_index]
		ask = [tools.read, tools.scan]

		assert len(extract) == 6
		assert len(maintain) == 6
		assert len(ask) == 2

	def test_tool_method_names(self, tools):
		"""Wrapper methods should expose stable names used by tests/docs."""
		methods = [
			tools.read,
			tools.grep,
			tools.scan,
			tools.write,
			tools.edit,
			tools.archive,
			tools.verify_index,
		]
		expected_names = {"read", "grep", "scan", "write", "edit", "archive", "verify_index"}
		seen = {m.__name__ for m in methods}
		assert seen == expected_names

	def test_memory_types_constant(self):
		"""MEMORY_TYPES includes all expected types."""
		assert "user" in MEMORY_TYPES
		assert "feedback" in MEMORY_TYPES
		assert "project" in MEMORY_TYPES
		assert "reference" in MEMORY_TYPES
		assert "summary" in MEMORY_TYPES


# ---------------------------------------------------------------------------
# Finding schema
# ---------------------------------------------------------------------------


class TestFinding:
	"""Finding is the atom of the agent's persistent reasoning state.

	These tests pin the schema shape so refactors can't silently drop a
	required field. The full RL-training story in the plan depends on
	theme/offset/quote being present on every finding.
	"""

	def test_finding_round_trip(self):
		"""Construct a Finding, serialize, deserialize, check field equality."""
		f = Finding(theme="framework migration", offset=142, quote="the three-pass was overengineered", level="decision")
		data = f.model_dump()
		assert data == {"theme": "framework migration", "offset": 142, "quote": "the three-pass was overengineered", "level": "decision"}
		f2 = Finding.model_validate(data)
		assert f2 == f

	def test_finding_requires_all_three_fields(self):
		"""Missing theme, offset, or quote should fail validation."""
		from pydantic import ValidationError

		with pytest.raises(ValidationError):
			Finding(theme="x", offset=0)  # type: ignore[call-arg]
		with pytest.raises(ValidationError):
			Finding(theme="x", quote="y")  # type: ignore[call-arg]
		with pytest.raises(ValidationError):
			Finding(offset=0, quote="y")  # type: ignore[call-arg]

	def test_finding_offset_is_int(self):
		"""offset must coerce to int; non-integer strings reject."""
		from pydantic import ValidationError

		f = Finding(theme="x", offset=42, quote="y", level="decision")
		assert f.offset == 42
		# Pydantic's default coercion accepts "42" → 42; non-numeric fails.
		with pytest.raises(ValidationError):
			Finding(theme="x", offset="not a number", quote="y", level="decision")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# note() tool — mutable state + tool call arg preservation
# ---------------------------------------------------------------------------


class TestNote:
	"""note() is the primary way the agent records findings.

	The tool writes findings into ctx.deps.notes (for programmatic access
	and the notes_state_injector to read) AND the findings also appear as
	tool call arguments in conversation history (for training signal /
	agent self-recall). Both locations matter and are independently tested.
	"""

	def test_note_appends_to_deps_notes(self, tmp_path):
		"""A single note() call extends deps.notes with the findings list."""
		deps = ExtractDeps(memory_root=tmp_path)
		ctx = SimpleNamespace(deps=deps)
		findings = [
			Finding(theme="migration", offset=10, quote="...", level="decision"),
			Finding(theme="migration", offset=42, quote="...", level="decision"),
		]
		ret = note(ctx, findings)
		assert deps.notes == findings
		assert "Noted 2 findings" in ret
		assert "total 2" in ret

	def test_note_accumulates_across_calls(self, tmp_path):
		"""Two note() calls with 3+2 findings produce deps.notes of length 5 in order."""
		deps = ExtractDeps(memory_root=tmp_path)
		ctx = SimpleNamespace(deps=deps)
		first = [Finding(theme="a", offset=1, quote="q1", level="decision") for _ in range(3)]
		second = [Finding(theme="b", offset=2, quote="q2", level="decision") for _ in range(2)]
		note(ctx, first)
		note(ctx, second)
		assert len(deps.notes) == 5
		assert deps.notes[:3] == first
		assert deps.notes[3:] == second

	def test_note_empty_list(self, tmp_path):
		"""note() with an empty list is a no-op that still returns a string."""
		deps = ExtractDeps(memory_root=tmp_path)
		ctx = SimpleNamespace(deps=deps)
		ret = note(ctx, [])
		assert deps.notes == []
		assert "Noted 0 findings" in ret


# ---------------------------------------------------------------------------
# prune() tool — mutable state + set-union semantics
# ---------------------------------------------------------------------------


class TestPrune:
	"""prune() marks trace offsets for stubbing via prune_history_processor.

	prune() only touches ctx.deps.pruned_offsets — it does NOT rewrite
	message history. That rewrite is done by prune_history_processor on
	the next turn. These tests cover the tool's own state-mutation
	contract; the processor behavior is tested separately below.
	"""

	def test_prune_populates_pruned_offsets(self, tmp_path):
		"""A single prune() call updates the set."""
		deps = ExtractDeps(memory_root=tmp_path)
		ctx = SimpleNamespace(deps=deps)
		ret = prune(ctx, [0, 100, 200])
		assert deps.pruned_offsets == {0, 100, 200}
		assert "Pruned 3 new" in ret

	def test_prune_set_union_semantics(self, tmp_path):
		"""Two prune() calls union their offsets; duplicates are not re-counted."""
		deps = ExtractDeps(memory_root=tmp_path)
		ctx = SimpleNamespace(deps=deps)
		prune(ctx, [0, 100])
		ret = prune(ctx, [100, 200])  # 100 is a duplicate
		assert deps.pruned_offsets == {0, 100, 200}
		# "new" should reflect only the genuinely-new additions (200, i.e. 1)
		assert "Pruned 1 new" in ret
		assert "total pruned: 3" in ret

	def test_prune_empty_list_is_noop(self, tmp_path):
		"""prune([]) returns a short no-op message without mutating state."""
		deps = ExtractDeps(memory_root=tmp_path)
		ctx = SimpleNamespace(deps=deps)
		ret = prune(ctx, [])
		assert deps.pruned_offsets == set()
		assert "No offsets" in ret


# ---------------------------------------------------------------------------
# compute_request_budget — auto-scaled request_limit
# ---------------------------------------------------------------------------


class TestComputeRequestBudget:
	"""Budget formula: max(50, min(100, 25 + 2*chunks))

	Where chunks = max(1, lines // 100). The 50-floor covers short-trace
	overhead plus the Agent's retries=3 buffer; the 100-ceiling protects
	against pathological inputs. Floor was bumped from 20→30→50 after
	smoke-testing on MiniMax showed small traces (137-line, 157-line)
	exhausting the tighter budgets.
	"""

	def _make_trace(self, tmp_path, n_lines: int) -> Path:
		trace = tmp_path / f"trace_{n_lines}.jsonl"
		trace.write_text("\n".join(["x"] * n_lines), encoding="utf-8")
		return trace

	@pytest.mark.parametrize(
		"n_lines,expected",
		[
			(50, 50),      # floor — tiny trace
			(100, 50),     # floor — exactly one chunk (25+2)
			(500, 50),     # floor — 25+10=35 below floor
			(1000, 50),    # floor — 25+20=45 below floor
			(2000, 65),    # 25 + 40
			(2165, 67),    # 25 + 42 — claude_028 outlier
			(10000, 100),  # 25 + 200 → ceiling
			(20000, 100),  # ceiling (well past)
		],
	)
	def test_budget_table(self, tmp_path, n_lines, expected):
		"""Table-driven budget test for representative trace sizes."""
		trace = self._make_trace(tmp_path, n_lines)
		assert compute_request_budget(trace) == expected

	def test_budget_unreadable_file_falls_back_safely(self, tmp_path):
		"""A non-existent path falls back to the 100-line estimate (floor 50)."""
		ghost = tmp_path / "ghost.jsonl"
		assert not ghost.exists()
		assert compute_request_budget(ghost) == 50


# ---------------------------------------------------------------------------
# History processors — test helpers
# ---------------------------------------------------------------------------


def _fake_ctx(deps: ExtractDeps) -> object:
	"""Lightweight ctx stand-in. Processors only read ctx.deps, so
	SimpleNamespace duck-types a real RunContext for unit tests.
	"""
	return SimpleNamespace(deps=deps)


def _make_model_request_with_user_prompt(text: str):
	"""Build a ModelRequest containing a single UserPromptPart with `text`."""
	from pydantic_ai.messages import ModelRequest, UserPromptPart

	return ModelRequest(
		parts=[UserPromptPart(content=text, timestamp=datetime.now(timezone.utc))]
	)


def _make_read_trace_call_and_return(
	offset: int,
	tool_call_id: str,
	chunk_text: str,
):
	"""Build a (ModelResponse(ToolCallPart), ModelRequest(ToolReturnPart)) pair.

	Matches the real shape PydanticAI produces for a read("trace", offset=N,
	limit=100) round-trip: a ModelResponse holding the ToolCallPart followed
	by a ModelRequest holding the matching ToolReturnPart.
	"""
	from pydantic_ai.messages import (
		ModelRequest,
		ModelResponse,
		ToolCallPart,
		ToolReturnPart,
	)

	call = ModelResponse(
		parts=[
			ToolCallPart(
				tool_name="read",
				args={"filename": "trace", "offset": offset, "limit": 100},
				tool_call_id=tool_call_id,
			)
		]
	)
	ret = ModelRequest(
		parts=[
			ToolReturnPart(
				tool_name="read",
				content=chunk_text,
				tool_call_id=tool_call_id,
				metadata=None,
				timestamp=datetime.now(timezone.utc),
			)
		]
	)
	return call, ret


# ---------------------------------------------------------------------------
# context_pressure_injector
# ---------------------------------------------------------------------------


class TestContextPressureInjector:
	"""Injects a CONTEXT: system message with graduated pressure labels.

	Label thresholds: <60% status only, 60-80% soft pressure, >80% hard.
	Size is estimated via a 4-char/token heuristic over all string-bearing
	message parts.
	"""

	def _messages_with_user_content(self, char_count: int):
		"""Build a minimal message list with one user prompt of the given size."""
		msg = _make_model_request_with_user_prompt("x" * char_count)
		return [msg]

	def test_low_pressure_status_only(self, tmp_path):
		"""Below 60% only the plain CONTEXT: X/Y (pct%) line appears."""
		deps = ExtractDeps(memory_root=tmp_path)
		ctx = _fake_ctx(deps)
		# 10K chars → ~2.5K tokens → well under 60% of 128K
		messages = self._messages_with_user_content(10_000)
		out = context_pressure_injector(ctx, messages)
		from pydantic_ai.messages import SystemPromptPart

		labels = [
			p.content for m in out
			for p in m.parts if isinstance(p, SystemPromptPart)
		]
		assert any("CONTEXT:" in line for line in labels)
		# No pressure wording
		for line in labels:
			if "CONTEXT:" in line:
				assert "pressure" not in line.lower()
				assert "HARD" not in line

	def test_soft_pressure_label(self, tmp_path):
		"""60-80% triggers soft pressure wording suggesting prune()."""
		deps = ExtractDeps(memory_root=tmp_path)
		ctx = _fake_ctx(deps)
		# Target ~65% of 128K → ~83K tokens → ~332K chars
		soft_chars = int(
			MODEL_CONTEXT_TOKEN_LIMIT * 4 * ((CONTEXT_SOFT_PRESSURE_PCT + CONTEXT_HARD_PRESSURE_PCT) / 2)
		)
		messages = self._messages_with_user_content(soft_chars)
		context_pressure_injector(ctx, messages)
		from pydantic_ai.messages import SystemPromptPart

		labels = [
			p.content for m in messages
			for p in m.parts if isinstance(p, SystemPromptPart)
		]
		ctx_labels = [line for line in labels if "CONTEXT:" in line]
		assert ctx_labels, "Expected at least one CONTEXT: label"
		assert any("soft pressure" in line for line in ctx_labels)
		assert not any("HARD PRESSURE" in line for line in ctx_labels)

	def test_hard_pressure_label(self, tmp_path):
		"""Above 80% triggers HARD PRESSURE wording demanding prune()."""
		deps = ExtractDeps(memory_root=tmp_path)
		ctx = _fake_ctx(deps)
		# Target ~90% of 128K
		hard_chars = int(MODEL_CONTEXT_TOKEN_LIMIT * 4 * 0.90)
		messages = self._messages_with_user_content(hard_chars)
		context_pressure_injector(ctx, messages)
		from pydantic_ai.messages import SystemPromptPart

		labels = [
			p.content for m in messages
			for p in m.parts if isinstance(p, SystemPromptPart)
		]
		ctx_labels = [line for line in labels if "CONTEXT:" in line]
		assert ctx_labels
		assert any("HARD PRESSURE" in line for line in ctx_labels)


# ---------------------------------------------------------------------------
# notes_state_injector
# ---------------------------------------------------------------------------


class TestNotesStateInjector:
	"""Injects a NOTES: line summarizing deps.notes grouped by theme.

	These tests pin the live-state invariant: mutating deps.notes between
	two processor calls must yield two different injected messages.
	"""

	def test_empty_notes_shows_zero(self, tmp_path):
		"""No findings yet → NOTES: 0 findings."""
		deps = ExtractDeps(memory_root=tmp_path)
		ctx = _fake_ctx(deps)
		messages = [_make_model_request_with_user_prompt("hi")]
		notes_state_injector(ctx, messages)
		from pydantic_ai.messages import SystemPromptPart

		labels = [
			p.content for m in messages
			for p in m.parts if isinstance(p, SystemPromptPart)
		]
		assert any("NOTES:" in line and "0 findings" in line for line in labels)

	def test_notes_grouped_by_theme(self, tmp_path):
		"""deps.notes with 8 findings across 3 themes produces a label listing all three."""
		deps = ExtractDeps(memory_root=tmp_path)
		ctx = _fake_ctx(deps)
		deps.notes.extend([
			Finding(theme="framework migration", offset=1, quote="a", level="decision"),
			Finding(theme="framework migration", offset=2, quote="b", level="decision"),
			Finding(theme="framework migration", offset=3, quote="c", level="decision"),
			Finding(theme="config refactor", offset=10, quote="d", level="implementation"),
			Finding(theme="config refactor", offset=11, quote="e", level="implementation"),
			Finding(theme="config refactor", offset=12, quote="f", level="implementation"),
			Finding(theme="config refactor", offset=13, quote="g", level="implementation"),
			Finding(theme="robustness", offset=20, quote="h", level="decision"),
		])
		messages = [_make_model_request_with_user_prompt("hi")]
		notes_state_injector(ctx, messages)
		from pydantic_ai.messages import SystemPromptPart

		labels = [
			p.content for m in messages
			for p in m.parts if isinstance(p, SystemPromptPart)
		]
		notes_line = next((line for line in labels if line.startswith("NOTES:")), None)
		assert notes_line is not None
		assert "8 findings" in notes_line
		assert "3 theme" in notes_line
		assert "framework migration" in notes_line
		assert "config refactor" in notes_line
		assert "robustness" in notes_line

	def test_reads_live_state_not_snapshot(self, tmp_path):
		"""Mutating deps.notes between two calls changes the injected label.

		This pins the core closure-free design: the processor must read
		ctx.deps.notes each call, not capture a snapshot.
		"""
		deps = ExtractDeps(memory_root=tmp_path)
		ctx = _fake_ctx(deps)

		# First call — 1 theme
		deps.notes.append(Finding(theme="theme1", offset=1, quote="q", level="decision"))
		messages_1 = [_make_model_request_with_user_prompt("turn 1")]
		notes_state_injector(ctx, messages_1)
		from pydantic_ai.messages import SystemPromptPart

		labels_1 = [
			p.content for m in messages_1
			for p in m.parts if isinstance(p, SystemPromptPart)
		]
		line_1 = next((line for line in labels_1 if line.startswith("NOTES:")), None)
		assert line_1 is not None
		assert "1 theme" in line_1

		# Mutate deps.notes (like a note() call between turns) — add a new theme.
		deps.notes.append(Finding(theme="theme2", offset=2, quote="q2", level="decision"))

		# Second call — should reflect the NEW state (2 themes)
		messages_2 = [_make_model_request_with_user_prompt("turn 2")]
		notes_state_injector(ctx, messages_2)
		labels_2 = [
			p.content for m in messages_2
			for p in m.parts if isinstance(p, SystemPromptPart)
		]
		line_2 = next((line for line in labels_2 if line.startswith("NOTES:")), None)
		assert line_2 is not None
		assert "2 theme" in line_2
		assert "theme2" in line_2


# ---------------------------------------------------------------------------
# prune_history_processor
# ---------------------------------------------------------------------------


class TestPruneHistoryProcessor:
	"""Rewrites trace-read ToolReturnPart.content to '[pruned]' for matching offsets.

	Tool calls themselves are preserved (agent still knows it read X);
	only the content is neutralized. Non-trace reads are never touched
	even if their offset argument happens to match.
	"""

	def test_rewrites_matching_trace_returns(self, tmp_path):
		"""Three read-trace tool pairs at offsets 0/100/200; prune {0, 100}; only 0 and 100 become '[pruned]'."""
		from pydantic_ai.messages import ToolReturnPart

		deps = ExtractDeps(memory_root=tmp_path, pruned_offsets={0, 100})
		ctx = _fake_ctx(deps)

		messages: list = []
		call_0, ret_0 = _make_read_trace_call_and_return(0, "c0", "chunk at 0")
		call_1, ret_1 = _make_read_trace_call_and_return(100, "c100", "chunk at 100")
		call_2, ret_2 = _make_read_trace_call_and_return(200, "c200", "chunk at 200")
		messages = [call_0, ret_0, call_1, ret_1, call_2, ret_2]

		prune_history_processor(ctx, messages)

		# Walk results and check content
		tr_parts = [
			p for m in messages
			for p in m.parts if isinstance(p, ToolReturnPart)
		]
		contents = {p.tool_call_id: p.content for p in tr_parts}
		assert contents["c0"] == PRUNED_STUB
		assert contents["c100"] == PRUNED_STUB
		assert contents["c200"] == "chunk at 200"

	def test_tool_call_parts_are_untouched(self, tmp_path):
		"""Tool CALLS stay intact even when their RETURNS are stubbed.

		The agent must still see that it read offset X on a prior turn —
		otherwise it might loop re-reading the same chunk.
		"""
		from pydantic_ai.messages import ToolCallPart

		deps = ExtractDeps(memory_root=tmp_path, pruned_offsets={0})
		ctx = _fake_ctx(deps)

		call_0, ret_0 = _make_read_trace_call_and_return(0, "c0", "chunk at 0")
		messages = [call_0, ret_0]
		prune_history_processor(ctx, messages)

		tc_parts = [
			p for m in messages
			for p in m.parts if isinstance(p, ToolCallPart)
		]
		assert len(tc_parts) == 1
		assert tc_parts[0].tool_name == "read"
		assert tc_parts[0].args == {"filename": "trace", "offset": 0, "limit": 100}
		assert tc_parts[0].tool_call_id == "c0"

	def test_non_trace_reads_unaffected(self, tmp_path):
		"""read('index.md') results are NEVER stubbed, even if offset matches."""
		from pydantic_ai.messages import (
			ModelRequest,
			ModelResponse,
			ToolCallPart,
			ToolReturnPart,
		)

		deps = ExtractDeps(memory_root=tmp_path, pruned_offsets={0})
		ctx = _fake_ctx(deps)

		# Synthetic read('index.md', offset=0) pair — should NOT be pruned.
		call = ModelResponse(
			parts=[ToolCallPart(
				tool_name="read",
				args={"filename": "index.md", "offset": 0},
				tool_call_id="idx0",
			)]
		)
		ret = ModelRequest(
			parts=[ToolReturnPart(
				tool_name="read",
				content="# Memory Index\n- foo.md\n",
				tool_call_id="idx0",
				metadata=None,
				timestamp=datetime.now(timezone.utc),
			)]
		)
		messages = [call, ret]
		prune_history_processor(ctx, messages)

		tr = [
			p for m in messages
			for p in m.parts if isinstance(p, ToolReturnPart)
		]
		assert tr[0].content == "# Memory Index\n- foo.md\n"

	def test_no_op_when_pruned_offsets_empty(self, tmp_path):
		"""Empty pruned_offsets → processor returns messages unchanged."""
		deps = ExtractDeps(memory_root=tmp_path)
		ctx = _fake_ctx(deps)
		call_0, ret_0 = _make_read_trace_call_and_return(0, "c0", "chunk at 0")
		messages = [call_0, ret_0]
		prune_history_processor(ctx, messages)

		from pydantic_ai.messages import ToolReturnPart
		tr = [
			p for m in messages
			for p in m.parts if isinstance(p, ToolReturnPart)
		]
		assert tr[0].content == "chunk at 0"

	def test_string_args_json_parse(self, tmp_path):
		"""ToolCallPart.args may be a JSON string (some providers) — parse and match."""
		from pydantic_ai.messages import (
			ModelRequest,
			ModelResponse,
			ToolCallPart,
			ToolReturnPart,
		)

		deps = ExtractDeps(memory_root=tmp_path, pruned_offsets={0})
		ctx = _fake_ctx(deps)

		call = ModelResponse(
			parts=[ToolCallPart(
				tool_name="read",
				args='{"filename": "trace", "offset": 0, "limit": 100}',
				tool_call_id="cstr",
			)]
		)
		ret = ModelRequest(
			parts=[ToolReturnPart(
				tool_name="read",
				content="chunk body",
				tool_call_id="cstr",
				metadata=None,
				timestamp=datetime.now(timezone.utc),
			)]
		)
		messages = [call, ret]
		prune_history_processor(ctx, messages)

		tr = [
			p for m in messages
			for p in m.parts if isinstance(p, ToolReturnPart)
		]
		assert tr[0].content == PRUNED_STUB
