"""Unit tests for MemoryTools (read, grep, scan, write, edit, archive)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lerim.agents.tools import MEMORY_TYPES, MemoryTools


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
	"""MemoryTools instance with memory root and trace."""
	return MemoryTools(memory_root=mem_root, trace_path=trace_file)


@pytest.fixture
def tools_no_trace(mem_root):
	"""MemoryTools instance without a trace path."""
	return MemoryTools(memory_root=mem_root)


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
		_write_memory_file(mem_root, "project_arch.md", "Architecture", "DSPy arch", "project")
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


# ---------------------------------------------------------------------------
# write
# ---------------------------------------------------------------------------


class TestWrite:
	def test_create_feedback(self, tools, mem_root):
		"""Write creates a new feedback memory file."""
		result = json.loads(tools.write(
			type="feedback", name="Use tabs",
			description="Tabs over spaces", body="Always use tabs.",
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
			type="project", name="DSPy migration",
			description="Migrated to DSPy ReAct",
			body="Migration completed.\n\n**Why:** Optimizable.\n\n**How to apply:** Use LerimRuntime.",
		))
		assert result["type"] == "project"
		assert (mem_root / result["filename"]).exists()

	def test_create_summary(self, tools, mem_root):
		"""Write with type=summary creates file in summaries/ subdir."""
		result = json.loads(tools.write(
			type="summary", name="Migration session",
			description="Migrated to DSPy",
			body="## User Intent\n\nMigrate runtime.\n\n## What Happened\n\nDone.",
		))
		assert result["type"] == "summary"
		assert (mem_root / "summaries" / result["filename"]).exists()

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
		            description="Tabs pref", body="Body.")
		result = tools.write(type="feedback", name="Use tabs",
		                     description="Dup attempt", body="Body.")
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
			result = json.loads(tools.write(
				type=t, name=f"Test {t}",
				description=f"Desc for {t}", body=f"Body for {t}.",
			))
			assert result["type"] == t


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


# ---------------------------------------------------------------------------
# DSPy tool introspection
# ---------------------------------------------------------------------------


class TestDspyIntrospection:
	def test_tools_are_callable_methods(self, tools):
		"""All tool methods are callable bound methods."""
		for method in [tools.read, tools.grep, tools.scan, tools.write, tools.edit, tools.archive]:
			assert callable(method)

	def test_tool_selection_per_agent(self, tools):
		"""Each agent gets the correct subset of tools."""
		extract = [tools.read, tools.grep, tools.scan, tools.write, tools.edit]
		maintain = [tools.read, tools.scan, tools.write, tools.edit, tools.archive]
		ask = [tools.read, tools.scan]

		assert len(extract) == 5
		assert len(maintain) == 5
		assert len(ask) == 2

	def test_dspy_tool_wrapping(self, tools):
		"""dspy.Tool should correctly wrap each method."""
		import dspy
		methods = [tools.read, tools.grep, tools.scan, tools.write, tools.edit, tools.archive]
		expected_names = {"read", "grep", "scan", "write", "edit", "archive"}
		seen = set()
		for method in methods:
			dt = dspy.Tool(method)
			seen.add(dt.name)
			assert dt.name != "partial"
		assert seen == expected_names

	def test_memory_types_constant(self):
		"""MEMORY_TYPES includes all expected types."""
		assert "user" in MEMORY_TYPES
		assert "feedback" in MEMORY_TYPES
		assert "project" in MEMORY_TYPES
		assert "reference" in MEMORY_TYPES
		assert "summary" in MEMORY_TYPES
