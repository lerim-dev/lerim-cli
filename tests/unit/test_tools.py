"""Unit tests for DSPy ReAct tools (write_memory, write_report, read_file, list_files,
archive_memory, edit_memory, scan_memory_manifest, update_memory_index,
make_extract_tools, make_maintain_tools, make_ask_tools)."""

from __future__ import annotations

import json
from functools import partial
from pathlib import Path

import pytest

from lerim.agents.context import RuntimeContext, build_context
from lerim.agents.tools import (
	archive_memory,
	make_ask_tools,
	make_maintain_tools,
	make_extract_tools,
	edit_memory,
	list_files,
	read_file,
	scan_memory_manifest,
	update_memory_index,
	write_memory,
	write_report,
)
from tests.helpers import make_config


def _make_ctx(tmp_path: Path, **overrides) -> RuntimeContext:
	"""Build a RuntimeContext for testing."""
	mem_root = tmp_path / "memories"
	mem_root.mkdir(exist_ok=True)
	run_folder = tmp_path / "runs" / "test-run"
	run_folder.mkdir(parents=True, exist_ok=True)

	defaults = dict(
		repo_root=tmp_path,
		memory_root=mem_root,
		workspace_root=tmp_path / "workspace",
		run_folder=run_folder,
		extra_read_roots=(),
		run_id="test-run-001",
		config=make_config(tmp_path),
	)
	defaults.update(overrides)
	return build_context(**defaults)


# ---------------------------------------------------------------------------
# write_memory: valid inputs
# ---------------------------------------------------------------------------


def test_write_memory_valid_project(tmp_path):
	"""Valid project memory should write a file and return JSON with file_path."""
	ctx = _make_ctx(tmp_path)
	result = write_memory(
		ctx,
		type="project",
		name="Use PostgreSQL",
		description="All persistence should use PostgreSQL for reliability.",
		body="All persistence should use PostgreSQL. **Why:** Battle-tested, excellent tooling.",
	)
	parsed = json.loads(result)
	assert parsed["type"] == "project"
	assert Path(parsed["file_path"]).exists()
	content = Path(parsed["file_path"]).read_text()
	assert "Use PostgreSQL" in content
	assert "type: project" in content


def test_write_memory_valid_feedback(tmp_path):
	"""Valid feedback memory should write correctly."""
	ctx = _make_ctx(tmp_path)
	result = write_memory(
		ctx,
		type="feedback",
		name="Queue heartbeat pattern",
		description="Keep heartbeat updates deterministic for reliability.",
		body="Keep heartbeat updates deterministic. **Why:** Non-deterministic heartbeats caused flaky tests.",
	)
	parsed = json.loads(result)
	assert parsed["type"] == "feedback"
	assert Path(parsed["file_path"]).exists()
	content = Path(parsed["file_path"]).read_text()
	assert "type: feedback" in content
	assert "name: Queue heartbeat pattern" in content


def test_write_memory_valid_user(tmp_path):
	"""Valid user memory should write correctly."""
	ctx = _make_ctx(tmp_path)
	result = write_memory(
		ctx,
		type="user",
		name="Prefers concise output",
		description="User wants terse responses, no padding.",
		body="User prefers concise, direct output. **Why:** Saves time and avoids noise.",
	)
	parsed = json.loads(result)
	assert parsed["type"] == "user"
	content = Path(parsed["file_path"]).read_text()
	assert "type: user" in content


def test_write_memory_valid_reference(tmp_path):
	"""Valid reference memory should write correctly."""
	ctx = _make_ctx(tmp_path)
	result = write_memory(
		ctx,
		type="reference",
		name="Logfire tracing dashboard",
		description="Logfire dashboard URL for observability.",
		body="Logfire tracing at https://logfire.pydantic.dev. Used for DSPy span analysis.",
	)
	parsed = json.loads(result)
	assert parsed["type"] == "reference"
	content = Path(parsed["file_path"]).read_text()
	assert "type: reference" in content


def test_write_memory_frontmatter_fields(tmp_path):
	"""write_memory should persist name, description, type in frontmatter."""
	ctx = _make_ctx(tmp_path)
	result = write_memory(
		ctx,
		type="project",
		name="Queue retries can fail safely",
		description="Use bounded retries for flaky queue workers.",
		body="Use bounded retries for flaky queue workers. **Why:** Unbounded retries caused cascading failures.",
	)
	parsed = json.loads(result)
	content = Path(parsed["file_path"]).read_text()
	assert "name: Queue retries can fail safely" in content
	assert "description: Use bounded retries for flaky queue workers." in content
	assert "type: project" in content
	# Old fields must NOT be present
	assert "confidence:" not in content
	assert "primitive:" not in content
	assert "kind:" not in content
	assert "tags:" not in content


def test_write_memory_flat_directory(tmp_path):
	"""write_memory should write files directly in memory_root (no subdirs)."""
	ctx = _make_ctx(tmp_path)
	result = write_memory(
		ctx,
		type="feedback",
		name="Flat dir test",
		description="Memory should be in flat directory.",
		body="Memories are stored flat under memory_root. No subdirectories by type.",
	)
	parsed = json.loads(result)
	file_path = Path(parsed["file_path"])
	# File should be directly in memory_root, not in a subdirectory
	assert file_path.parent == ctx.memory_root


# ---------------------------------------------------------------------------
# write_memory: validation errors
# ---------------------------------------------------------------------------


def test_write_memory_invalid_type(tmp_path):
	"""Invalid type should return an ERROR string."""
	ctx = _make_ctx(tmp_path)
	result = write_memory(
		ctx,
		type="fact",
		name="Bad",
		description="Bad description.",
		body="Bad body content here.",
	)
	assert result.startswith("ERROR:")
	assert "user" in result
	assert "feedback" in result


def test_write_memory_empty_name(tmp_path):
	"""Empty name should return an ERROR string."""
	ctx = _make_ctx(tmp_path)
	result = write_memory(
		ctx,
		type="project",
		name="",
		description="Some description.",
		body="No name provided.",
	)
	assert result.startswith("ERROR:")
	assert "name" in result


def test_write_memory_empty_description(tmp_path):
	"""Empty description should return an ERROR string."""
	ctx = _make_ctx(tmp_path)
	result = write_memory(
		ctx,
		type="project",
		name="No description test",
		description="",
		body="Missing description.",
	)
	assert result.startswith("ERROR:")
	assert "description" in result


def test_write_memory_empty_body(tmp_path):
	"""Empty body should return an ERROR string."""
	ctx = _make_ctx(tmp_path)
	result = write_memory(
		ctx,
		type="project",
		name="No body test",
		description="This has no body.",
		body="",
	)
	assert result.startswith("ERROR:")
	assert "body" in result


def test_write_memory_no_memory_root(tmp_path):
	"""Missing memory_root in context should return an ERROR string."""
	ctx = build_context(
		repo_root=tmp_path,
		config=make_config(tmp_path),
	)
	result = write_memory(
		ctx,
		type="project",
		name="No root",
		description="Should fail without memory_root.",
		body="Should fail because memory_root is not set.",
	)
	assert result.startswith("ERROR:")
	assert "memory_root" in result


# ---------------------------------------------------------------------------
# write_report tests
# ---------------------------------------------------------------------------


def test_write_report_valid(tmp_path):
	"""write_report should write valid JSON to workspace."""
	ctx = _make_ctx(tmp_path)
	report_path = str(ctx.run_folder / "memory_actions.json")
	content = json.dumps({"counts": {"add": 1, "update": 0, "no_op": 0}})
	result = write_report(ctx, file_path=report_path, content=content)
	assert "Report written" in result
	written = json.loads(Path(report_path).read_text())
	assert written["counts"]["add"] == 1


def test_write_report_outside_workspace(tmp_path):
	"""write_report should reject paths outside run_folder."""
	ctx = _make_ctx(tmp_path)
	result = write_report(
		ctx,
		file_path="/tmp/evil.json",
		content="{}",
	)
	assert "Error" in result
	assert "outside" in result


def test_write_report_invalid_json(tmp_path):
	"""write_report should reject invalid JSON content."""
	ctx = _make_ctx(tmp_path)
	report_path = str(ctx.run_folder / "bad.json")
	result = write_report(ctx, file_path=report_path, content="not json")
	assert "Error" in result
	assert "not valid JSON" in result


# ---------------------------------------------------------------------------
# read_file tests
# ---------------------------------------------------------------------------


def test_read_file_from_memory_root(tmp_path):
	"""read_file should read files within memory_root."""
	ctx = _make_ctx(tmp_path)
	test_file = ctx.memory_root / "test-decision.md"
	test_file.write_text("# Test Decision\nSome content.")
	result = read_file(ctx, file_path=str(test_file))
	assert "Test Decision" in result


def test_read_file_from_run_folder(tmp_path):
	"""read_file should read files within run_folder."""
	ctx = _make_ctx(tmp_path)
	test_file = ctx.run_folder / "test_data.json"
	test_file.write_text('{"candidates": []}')
	result = read_file(ctx, file_path=str(test_file))
	assert "candidates" in result


def test_read_file_outside_roots(tmp_path):
	"""read_file should reject paths outside allowed roots."""
	ctx = _make_ctx(tmp_path)
	result = read_file(ctx, file_path="/etc/passwd")
	assert "Error" in result
	assert "outside" in result


def test_read_file_not_found(tmp_path):
	"""read_file should return error for missing files."""
	ctx = _make_ctx(tmp_path)
	result = read_file(ctx, file_path=str(ctx.memory_root / "nonexistent.md"))
	assert "Error" in result
	assert "not found" in result


# ---------------------------------------------------------------------------
# list_files tests
# ---------------------------------------------------------------------------


def test_list_files_in_memory_root(tmp_path):
	"""list_files should list files in memory directories."""
	ctx = _make_ctx(tmp_path)
	(ctx.memory_root / "a.md").write_text("# A")
	(ctx.memory_root / "b.md").write_text("# B")
	result = list_files(ctx, directory=str(ctx.memory_root))
	files = json.loads(result)
	assert len(files) == 2


def test_list_files_empty_dir(tmp_path):
	"""list_files should return empty list for empty directory."""
	ctx = _make_ctx(tmp_path)
	result = list_files(ctx, directory=str(ctx.memory_root))
	files = json.loads(result)
	assert files == []


def test_list_files_missing_dir(tmp_path):
	"""list_files should return empty list for missing directory."""
	ctx = _make_ctx(tmp_path)
	result = list_files(ctx, directory=str(ctx.memory_root / "nonexistent"))
	assert result == "[]"


def test_list_files_outside_roots(tmp_path):
	"""list_files should reject paths outside allowed roots."""
	ctx = _make_ctx(tmp_path)
	result = list_files(ctx, directory="/tmp")
	assert "Error" in result
	assert "outside" in result


def test_list_files_with_pattern(tmp_path):
	"""list_files should filter by glob pattern."""
	ctx = _make_ctx(tmp_path)
	(ctx.memory_root / "a.md").write_text("# A")
	(ctx.memory_root / "b.json").write_text("{}")
	result = list_files(
		ctx,
		directory=str(ctx.memory_root),
		pattern="*.json",
	)
	files = json.loads(result)
	assert len(files) == 1
	assert files[0].endswith(".json")


# ---------------------------------------------------------------------------
# archive_memory tests
# ---------------------------------------------------------------------------


def test_archive_memory_flat(tmp_path):
	"""archive_memory should move a flat memory file to archived/."""
	ctx = _make_ctx(tmp_path)
	src = ctx.memory_root / "20260327-test-decision.md"
	src.write_text("---\nname: Test\ntype: project\n---\nBody")
	result = archive_memory(ctx, file_path=str(src))
	parsed = json.loads(result)
	assert parsed["archived"] is True
	assert not src.exists()
	target = Path(parsed["target"])
	assert target.exists()
	assert "archived/20260327-test-decision.md" in str(target)


def test_archive_memory_outside_memory_root(tmp_path):
	"""archive_memory should reject paths outside memory_root."""
	ctx = _make_ctx(tmp_path)
	result = archive_memory(ctx, file_path="/tmp/evil.md")
	assert result.startswith("ERROR:")
	assert "outside" in result


def test_archive_memory_not_found(tmp_path):
	"""archive_memory should error on missing files."""
	ctx = _make_ctx(tmp_path)
	result = archive_memory(
		ctx,
		file_path=str(ctx.memory_root / "gone.md"),
	)
	assert result.startswith("ERROR:")
	assert "not found" in result


def test_archive_memory_no_memory_root(tmp_path):
	"""archive_memory should error when memory_root is not set."""
	ctx = build_context(repo_root=tmp_path, config=make_config(tmp_path))
	result = archive_memory(ctx, file_path="/some/path.md")
	assert result.startswith("ERROR:")
	assert "memory_root" in result


# ---------------------------------------------------------------------------
# edit_memory tests
# ---------------------------------------------------------------------------


def test_edit_memory_updates_content(tmp_path):
	"""edit_memory should replace file content."""
	ctx = _make_ctx(tmp_path)
	target = ctx.memory_root / "edit-test.md"
	target.write_text("---\nname: Old\ntype: project\n---\nOld body")
	new_content = "---\nname: Old\ntype: project\ndescription: Updated\n---\nNew body"
	result = edit_memory(ctx, file_path=str(target), new_content=new_content)
	parsed = json.loads(result)
	assert parsed["edited"] is True
	assert target.read_text() == new_content


def test_edit_memory_rejects_no_frontmatter(tmp_path):
	"""edit_memory should reject content without YAML frontmatter."""
	ctx = _make_ctx(tmp_path)
	target = ctx.memory_root / "edit-test.md"
	target.write_text("---\nname: Old\n---\nBody")
	result = edit_memory(
		ctx,
		file_path=str(target),
		new_content="No frontmatter here",
	)
	assert result.startswith("ERROR:")
	assert "frontmatter" in result


def test_edit_memory_outside_memory_root(tmp_path):
	"""edit_memory should reject paths outside memory_root."""
	ctx = _make_ctx(tmp_path)
	result = edit_memory(
		ctx,
		file_path="/tmp/evil.md",
		new_content="---\n---",
	)
	assert result.startswith("ERROR:")
	assert "outside" in result


def test_edit_memory_not_found(tmp_path):
	"""edit_memory should error on missing files."""
	ctx = _make_ctx(tmp_path)
	result = edit_memory(
		ctx,
		file_path=str(ctx.memory_root / "gone.md"),
		new_content="---\nname: Gone\n---\nBody",
	)
	assert result.startswith("ERROR:")
	assert "not found" in result


def test_edit_memory_no_memory_root(tmp_path):
	"""edit_memory should error when memory_root is not set."""
	ctx = build_context(repo_root=tmp_path, config=make_config(tmp_path))
	result = edit_memory(
		ctx,
		file_path="/some/path.md",
		new_content="---\n---",
	)
	assert result.startswith("ERROR:")
	assert "memory_root" in result


# ---------------------------------------------------------------------------
# scan_memory_manifest tests
# ---------------------------------------------------------------------------


def _write_test_memory(
	memory_root, memory_id, name, body, mem_type="project",
):
	"""Write a minimal memory markdown file for testing (flat directory)."""
	content = f"""---
id: {memory_id}
name: {name}
description: {name}
type: {mem_type}
created: '2026-03-27T00:00:00+00:00'
updated: '2026-03-27T00:00:00+00:00'
source: test-run
---

{body}
"""
	path = memory_root / f"20260327-{memory_id}.md"
	path.parent.mkdir(parents=True, exist_ok=True)
	path.write_text(content, encoding="utf-8")
	return path


def test_scan_memory_manifest_returns_all(tmp_path):
	"""scan_memory_manifest returns metadata for all .md files except MEMORY.md."""
	ctx = _make_ctx(tmp_path)
	_write_test_memory(ctx.memory_root, "use-postgres", "Use PostgreSQL", "Chose PostgreSQL.")
	_write_test_memory(ctx.memory_root, "pytest-tips", "Pytest tips", "Use fixtures.", mem_type="feedback")
	# MEMORY.md should be excluded
	(ctx.memory_root / "MEMORY.md").write_text("# Index\n")

	result = scan_memory_manifest(ctx)
	parsed = json.loads(result)
	assert parsed["count"] == 2
	names = {m["name"] for m in parsed["memories"]}
	assert "Use PostgreSQL" in names
	assert "Pytest tips" in names


def test_scan_memory_manifest_empty(tmp_path):
	"""scan_memory_manifest on empty memory_root returns count 0."""
	ctx = _make_ctx(tmp_path)
	result = scan_memory_manifest(ctx)
	parsed = json.loads(result)
	assert parsed["count"] == 0
	assert parsed["memories"] == []


def test_scan_memory_manifest_no_memory_root(tmp_path):
	"""scan_memory_manifest without memory_root returns error."""
	ctx = build_context(repo_root=tmp_path, config=make_config(tmp_path))
	result = scan_memory_manifest(ctx)
	parsed = json.loads(result)
	assert "error" in parsed


# ---------------------------------------------------------------------------
# update_memory_index tests
# ---------------------------------------------------------------------------


def test_update_memory_index_writes_file(tmp_path):
	"""update_memory_index should write MEMORY.md."""
	ctx = _make_ctx(tmp_path)
	content = "- [Use PostgreSQL](use-postgres.md) -- Primary database choice"
	result = update_memory_index(ctx, content)
	parsed = json.loads(result)
	assert parsed["lines"] >= 1
	assert parsed["bytes"] > 0
	index_path = Path(parsed["file_path"])
	assert index_path.exists()
	assert index_path.name == "MEMORY.md"
	assert "Use PostgreSQL" in index_path.read_text()


def test_update_memory_index_truncates_long(tmp_path):
	"""update_memory_index should truncate content beyond 200 lines."""
	ctx = _make_ctx(tmp_path)
	lines = [f"- [Memory {i}](mem-{i}.md) -- description {i}" for i in range(250)]
	content = "\n".join(lines)
	result = update_memory_index(ctx, content)
	parsed = json.loads(result)
	# 200 original + 1 warning line + trailing newline
	assert parsed["lines"] <= 203


def test_update_memory_index_no_memory_root(tmp_path):
	"""update_memory_index without memory_root returns error."""
	ctx = build_context(repo_root=tmp_path, config=make_config(tmp_path))
	result = update_memory_index(ctx, "# Index")
	parsed = json.loads(result)
	assert "error" in parsed


# ---------------------------------------------------------------------------
# Bind helper tests
# ---------------------------------------------------------------------------


def test_closure_tools_have_function_names(tmp_path):
	"""Closure-bound tools should have proper function names, not 'partial'."""
	ctx = _make_ctx(tmp_path)
	tools = make_extract_tools(ctx)
	for tool in tools:
		assert tool.__name__ != "partial"
		assert tool.__name__ != "<lambda>"


@pytest.mark.parametrize("bind_fn,expected_count", [
	(make_extract_tools, 11),
	(make_maintain_tools, 8),
	(make_ask_tools, 3),
])
def test_bind_tools_callable(tmp_path, bind_fn, expected_count):
	"""Bound tools should be callable with preserved names."""
	ctx = _make_ctx(tmp_path)
	tools = bind_fn(ctx)
	assert len(tools) == expected_count
	for tool in tools:
		assert callable(tool)
		assert hasattr(tool, "__name__")
		assert tool.__name__ != "partial"


def test_bind_tools_dspy_introspection(tmp_path):
	"""dspy.Tool should see correct names and args from bound tools."""
	import dspy
	ctx = _make_ctx(tmp_path)
	tools = make_extract_tools(ctx)
	expected_names = {
		"read_file", "read_trace", "grep_trace",
		"scan_memory_manifest", "write_memory", "write_summary",
		"edit_memory", "archive_memory", "update_memory_index",
		"list_files", "write_report",
	}
	seen_names = set()
	for tool in tools:
		dt = dspy.Tool(tool)
		seen_names.add(dt.name)
		assert dt.name != "partial", f"dspy.Tool name should not be 'partial'"
		assert "args" not in dt.args or len(dt.args) > 1, f"{dt.name}: args should not be generic"
	assert seen_names == expected_names
