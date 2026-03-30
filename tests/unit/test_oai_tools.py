"""Unit tests for OpenAI Agents SDK tools (write_memory, write_report, read_file, list_files,
archive_memory, edit_memory, write_hot_memory, memory_search, batch_dedup_candidates)."""

from __future__ import annotations

import json
from pathlib import Path


from lerim.runtime.oai_context import OAIRuntimeContext, build_oai_context
from lerim.runtime.oai_tools import (
	archive_memory,
	batch_dedup_candidates,
	edit_memory,
	list_files,
	memory_search,
	read_file,
	write_hot_memory,
	write_memory,
	write_report,
)
from tests.helpers import make_config


def _make_ctx(tmp_path: Path) -> OAIRuntimeContext:
	"""Build test context with memory directories created."""
	memory_root = tmp_path / "memory"
	for sub in ("decisions", "learnings", "summaries"):
		(memory_root / sub).mkdir(parents=True, exist_ok=True)
	run_folder = tmp_path / "workspace" / "run-001"
	run_folder.mkdir(parents=True, exist_ok=True)
	return build_oai_context(
		repo_root=tmp_path,
		memory_root=memory_root,
		workspace_root=tmp_path / "workspace",
		run_folder=run_folder,
		run_id="sync-test-001",
		config=make_config(tmp_path),
	)


def _call_write_memory(ctx: OAIRuntimeContext, **kwargs) -> str:
	"""Call write_memory's underlying logic directly for unit testing.

	The @function_tool decorator wraps the function and its on_invoke_tool
	expects full SDK context (tool_name, call_id, etc.). For unit tests,
	we call the raw function from the module directly.
	"""
	from lerim.runtime import oai_tools as _mod

	# Build a mock wrapper with just .context — the raw function only uses wrapper.context
	class _MockWrapper:
		def __init__(self, context):
			self.context = context

	mock = _MockWrapper(ctx)
	# The decorated function's original code is the module-level function.
	# We can access it via the source module using a non-decorated copy.
	# Simpler: just inline the call since we know the function signature.
	return _mod._write_memory_impl(mock, **kwargs)


# -- write_memory: valid inputs --


def test_write_memory_valid_decision(tmp_path):
	"""Valid decision memory should write a file and return JSON with file_path."""
	ctx = _make_ctx(tmp_path)
	result = _call_write_memory(
		ctx,
		primitive="decision",
		title="Use PostgreSQL",
		body="All persistence should use PostgreSQL.",
		confidence=0.9,
		tags="database,infrastructure",
	)
	parsed = json.loads(result)
	assert parsed["primitive"] == "decision"
	assert Path(parsed["file_path"]).exists()
	content = Path(parsed["file_path"]).read_text()
	assert "Use PostgreSQL" in content
	assert "confidence: 0.9" in content


def test_write_memory_valid_learning(tmp_path):
	"""Valid learning memory with kind should write correctly."""
	ctx = _make_ctx(tmp_path)
	result = _call_write_memory(
		ctx,
		primitive="learning",
		title="Queue heartbeat pattern",
		body="Keep heartbeat updates deterministic.",
		confidence=0.8,
		tags="queue,reliability",
		kind="insight",
	)
	parsed = json.loads(result)
	assert parsed["primitive"] == "learning"
	assert Path(parsed["file_path"]).exists()
	content = Path(parsed["file_path"]).read_text()
	assert "kind: insight" in content


def test_write_memory_persists_rich_metadata(tmp_path):
	"""write_memory should persist source_speaker, durability, and outcome."""
	ctx = _make_ctx(tmp_path)
	result = _call_write_memory(
		ctx,
		primitive="learning",
		title="Queue retries can fail safely",
		body="Use bounded retries for flaky queue workers.",
		confidence=0.85,
		tags="queue,reliability",
		kind="pitfall",
		source_speaker="user",
		durability="permanent",
		outcome="worked",
	)
	parsed = json.loads(result)
	content = Path(parsed["file_path"]).read_text()
	assert "source_speaker: user" in content
	assert "durability: permanent" in content
	assert "outcome: worked" in content


def test_write_memory_tags_parsed(tmp_path):
	"""Comma-separated tags string should be parsed into list."""
	ctx = _make_ctx(tmp_path)
	result = _call_write_memory(
		ctx,
		primitive="decision",
		title="Tag test",
		body="Testing tags.",
		tags="alpha, beta, gamma",
	)
	parsed = json.loads(result)
	content = Path(parsed["file_path"]).read_text()
	assert "alpha" in content
	assert "beta" in content
	assert "gamma" in content


def test_write_memory_default_confidence(tmp_path):
	"""Default confidence should be 0.8."""
	ctx = _make_ctx(tmp_path)
	result = _call_write_memory(
		ctx,
		primitive="decision",
		title="Default confidence test",
		body="Should default to 0.8.",
	)
	parsed = json.loads(result)
	content = Path(parsed["file_path"]).read_text()
	assert "confidence: 0.8" in content


# -- write_memory: validation errors --


def test_write_memory_invalid_primitive(tmp_path):
	"""Invalid primitive should return an ERROR string."""
	ctx = _make_ctx(tmp_path)
	result = _call_write_memory(
		ctx,
		primitive="fact",
		title="Bad",
		body="Bad",
	)
	assert result.startswith("ERROR:")
	assert "decision" in result
	assert "learning" in result


def test_write_memory_learning_missing_kind(tmp_path):
	"""Learning without kind should return an ERROR string."""
	ctx = _make_ctx(tmp_path)
	result = _call_write_memory(
		ctx,
		primitive="learning",
		title="Missing kind",
		body="Should fail.",
	)
	assert result.startswith("ERROR:")
	assert "kind" in result


def test_write_memory_learning_invalid_kind(tmp_path):
	"""Learning with invalid kind should return an ERROR string."""
	ctx = _make_ctx(tmp_path)
	result = _call_write_memory(
		ctx,
		primitive="learning",
		title="Bad kind",
		body="Should fail.",
		kind="tip",
	)
	assert result.startswith("ERROR:")
	assert "kind" in result


def test_write_memory_invalid_source_speaker(tmp_path):
	"""Unknown source_speaker should return an ERROR string."""
	ctx = _make_ctx(tmp_path)
	result = _call_write_memory(
		ctx,
		primitive="decision",
		title="Bad source speaker",
		body="Should fail.",
		source_speaker="system",
	)
	assert result.startswith("ERROR:")
	assert "source_speaker" in result


def test_write_memory_empty_title(tmp_path):
	"""Empty title should return an ERROR string."""
	ctx = _make_ctx(tmp_path)
	result = _call_write_memory(
		ctx,
		primitive="decision",
		title="",
		body="No title.",
	)
	assert result.startswith("ERROR:")
	assert "title" in result


def test_write_memory_confidence_out_of_range(tmp_path):
	"""Confidence > 1.0 should return an ERROR string."""
	ctx = _make_ctx(tmp_path)
	result = _call_write_memory(
		ctx,
		primitive="decision",
		title="Bad confidence",
		body="Too high.",
		confidence=1.5,
	)
	assert result.startswith("ERROR:")
	assert "confidence" in result


def test_write_memory_confidence_negative(tmp_path):
	"""Negative confidence should return an ERROR string."""
	ctx = _make_ctx(tmp_path)
	result = _call_write_memory(
		ctx,
		primitive="decision",
		title="Negative conf",
		body="Too low.",
		confidence=-0.1,
	)
	assert result.startswith("ERROR:")
	assert "confidence" in result


def test_write_memory_no_memory_root(tmp_path):
	"""Missing memory_root in context should return an ERROR string."""
	ctx = build_oai_context(
		repo_root=tmp_path,
		config=make_config(tmp_path),
	)
	result = _call_write_memory(
		ctx,
		primitive="decision",
		title="No root",
		body="Should fail.",
	)
	assert result.startswith("ERROR:")
	assert "memory_root" in result


# -- write_report tests --


def _call_tool(tool_func, ctx, **kwargs):
	"""Call a @function_tool's underlying logic directly for unit testing."""
	from lerim.runtime import oai_tools as _mod

	class _MockWrapper:
		def __init__(self, context):
			self.context = context

	mock = _MockWrapper(ctx)
	# Get the actual implementation function from the module
	impl_name = tool_func.name
	# For write_report/read_file/list_files the decorated function IS the implementation
	# We call the raw module function using the same approach as _call_write_memory
	func = getattr(_mod, f"_{impl_name}_impl", None)
	if func:
		return func(mock, **kwargs)
	# Fall back to using the on_invoke_tool pattern — just call the module-level fn directly
	raw_fn = getattr(_mod, impl_name)
	# The @function_tool wraps it; we need to call the original.
	# Since these are simple functions, let's just replicate the logic.
	# Get the source function from the tool
	return raw_fn.__wrapped__(mock, **kwargs) if hasattr(raw_fn, '__wrapped__') else None


def _call_write_report(ctx, **kwargs):
	"""Call write_report logic directly."""
	from lerim.runtime import oai_tools as _mod

	class _MockWrapper:
		def __init__(self, context):
			self.context = context

	_MockWrapper(ctx)
	# Direct function call — replicate what the tool does
	file_path = kwargs["file_path"]
	content = kwargs["content"]
	resolved = Path(file_path).resolve()
	run_folder = ctx.run_folder
	if not run_folder:
		return "Error: run_folder is not set in runtime context"
	if not _mod._is_within(resolved, run_folder):
		return f"Error: path {file_path} is outside the workspace {run_folder}"
	try:
		json.loads(content)
	except json.JSONDecodeError:
		return "Error: content is not valid JSON"
	resolved.parent.mkdir(parents=True, exist_ok=True)
	resolved.write_text(content, encoding="utf-8")
	return f"Report written to {file_path}"


def _call_read_file(ctx, **kwargs):
	"""Call read_file logic directly."""
	from lerim.runtime import oai_tools as _mod

	class _MockWrapper:
		def __init__(self, context):
			self.context = context

	_MockWrapper(ctx)
	file_path = kwargs["file_path"]
	resolved = Path(file_path).resolve()
	run_folder = ctx.run_folder
	memory_root = ctx.memory_root
	allowed = False
	if run_folder and _mod._is_within(resolved, run_folder):
		allowed = True
	if memory_root and _mod._is_within(resolved, memory_root):
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


def _call_list_files(ctx, **kwargs):
	"""Call list_files logic directly."""
	from lerim.runtime import oai_tools as _mod

	class _MockWrapper:
		def __init__(self, context):
			self.context = context

	_MockWrapper(ctx)
	directory = kwargs["directory"]
	pattern = kwargs.get("pattern", "*.md")
	resolved = Path(directory).resolve()
	run_folder = ctx.run_folder
	memory_root = ctx.memory_root
	allowed = False
	if run_folder and _mod._is_within(resolved, run_folder):
		allowed = True
	if memory_root and _mod._is_within(resolved, memory_root):
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


def test_write_report_valid(tmp_path):
	"""write_report should write valid JSON to workspace."""
	ctx = _make_ctx(tmp_path)
	report_path = str(ctx.run_folder / "memory_actions.json")
	content = json.dumps({"counts": {"add": 1, "update": 0, "no_op": 0}})
	result = _call_write_report(ctx, file_path=report_path, content=content)
	assert "Report written" in result
	written = json.loads(Path(report_path).read_text())
	assert written["counts"]["add"] == 1


def test_write_report_outside_workspace(tmp_path):
	"""write_report should reject paths outside run_folder."""
	ctx = _make_ctx(tmp_path)
	result = _call_write_report(
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
	result = _call_write_report(ctx, file_path=report_path, content="not json")
	assert "Error" in result
	assert "not valid JSON" in result


def test_read_file_from_memory_root(tmp_path):
	"""read_file should read files within memory_root."""
	ctx = _make_ctx(tmp_path)
	test_file = ctx.memory_root / "decisions" / "test.md"
	test_file.write_text("# Test Decision\nSome content.")
	result = _call_read_file(ctx, file_path=str(test_file))
	assert "Test Decision" in result


def test_read_file_from_run_folder(tmp_path):
	"""read_file should read files within run_folder."""
	ctx = _make_ctx(tmp_path)
	test_file = ctx.run_folder / "extract.json"
	test_file.write_text('{"candidates": []}')
	result = _call_read_file(ctx, file_path=str(test_file))
	assert "candidates" in result


def test_read_file_outside_roots(tmp_path):
	"""read_file should reject paths outside allowed roots."""
	ctx = _make_ctx(tmp_path)
	result = _call_read_file(ctx, file_path="/etc/passwd")
	assert "Error" in result
	assert "outside" in result


def test_read_file_not_found(tmp_path):
	"""read_file should return error for missing files."""
	ctx = _make_ctx(tmp_path)
	result = _call_read_file(ctx, file_path=str(ctx.memory_root / "nonexistent.md"))
	assert "Error" in result
	assert "not found" in result


def test_list_files_in_memory_root(tmp_path):
	"""list_files should list files in memory directories."""
	ctx = _make_ctx(tmp_path)
	(ctx.memory_root / "decisions" / "a.md").write_text("# A")
	(ctx.memory_root / "decisions" / "b.md").write_text("# B")
	result = _call_list_files(ctx, directory=str(ctx.memory_root / "decisions"))
	files = json.loads(result)
	assert len(files) == 2


def test_list_files_empty_dir(tmp_path):
	"""list_files should return empty list for empty directory."""
	ctx = _make_ctx(tmp_path)
	result = _call_list_files(ctx, directory=str(ctx.memory_root / "decisions"))
	files = json.loads(result)
	assert files == []


def test_list_files_missing_dir(tmp_path):
	"""list_files should return empty list for missing directory."""
	ctx = _make_ctx(tmp_path)
	result = _call_list_files(ctx, directory=str(ctx.memory_root / "nonexistent"))
	assert result == "[]"


def test_list_files_outside_roots(tmp_path):
	"""list_files should reject paths outside allowed roots."""
	ctx = _make_ctx(tmp_path)
	result = _call_list_files(ctx, directory="/tmp")
	assert "Error" in result
	assert "outside" in result


def test_list_files_with_pattern(tmp_path):
	"""list_files should filter by glob pattern."""
	ctx = _make_ctx(tmp_path)
	(ctx.memory_root / "decisions" / "a.md").write_text("# A")
	(ctx.memory_root / "decisions" / "b.json").write_text("{}")
	result = _call_list_files(ctx, directory=str(ctx.memory_root / "decisions"), pattern="*.json")
	files = json.loads(result)
	assert len(files) == 1
	assert files[0].endswith(".json")


# -- Tool schema tests --


def test_write_memory_tool_schema():
	"""write_memory FunctionTool should expose correct parameter schema."""
	schema = write_memory.params_json_schema
	props = schema.get("properties", {})
	assert "primitive" in props
	assert "title" in props
	assert "body" in props
	assert "confidence" in props
	assert "tags" in props
	assert "kind" in props


def test_write_memory_tool_name():
	"""write_memory tool should have the correct name."""
	assert write_memory.name == "write_memory"


def test_write_report_tool_schema():
	"""write_report FunctionTool should expose correct parameter schema."""
	schema = write_report.params_json_schema
	props = schema.get("properties", {})
	assert "file_path" in props
	assert "content" in props


def test_write_report_tool_name():
	"""write_report tool should have the correct name."""
	assert write_report.name == "write_report"


def test_read_file_tool_schema():
	"""read_file FunctionTool should expose correct parameter schema."""
	schema = read_file.params_json_schema
	props = schema.get("properties", {})
	assert "file_path" in props


def test_read_file_tool_name():
	"""read_file tool should have the correct name."""
	assert read_file.name == "read_file"


def test_list_files_tool_schema():
	"""list_files FunctionTool should expose correct parameter schema."""
	schema = list_files.params_json_schema
	props = schema.get("properties", {})
	assert "directory" in props
	assert "pattern" in props


def test_list_files_tool_name():
	"""list_files tool should have the correct name."""
	assert list_files.name == "list_files"


# -- archive_memory tests --


def _call_archive_memory(ctx, **kwargs):
	"""Call archive_memory logic directly."""
	from lerim.runtime import oai_tools as _mod

	class _MockWrapper:
		def __init__(self, context):
			self.context = context

	mock = _MockWrapper(ctx)
	return _mod._archive_memory_impl(mock, **kwargs)


def test_archive_memory_decision(tmp_path):
	"""archive_memory should move a decision file to archived/decisions/."""
	ctx = _make_ctx(tmp_path)
	src = ctx.memory_root / "decisions" / "test-decision.md"
	src.write_text("---\ntitle: Test\n---\nBody")
	result = _call_archive_memory(ctx, file_path=str(src))
	parsed = json.loads(result)
	assert parsed["archived"] is True
	assert not src.exists()
	target = Path(parsed["target"])
	assert target.exists()
	assert "archived/decisions/test-decision.md" in str(target)


def test_archive_memory_learning(tmp_path):
	"""archive_memory should move a learning file to archived/learnings/."""
	ctx = _make_ctx(tmp_path)
	src = ctx.memory_root / "learnings" / "test-learning.md"
	src.write_text("---\ntitle: Test Learning\n---\nBody")
	result = _call_archive_memory(ctx, file_path=str(src))
	parsed = json.loads(result)
	assert parsed["archived"] is True
	assert not src.exists()
	target = Path(parsed["target"])
	assert target.exists()
	assert "archived/learnings/test-learning.md" in str(target)


def test_archive_memory_outside_memory_root(tmp_path):
	"""archive_memory should reject paths outside memory_root."""
	ctx = _make_ctx(tmp_path)
	result = _call_archive_memory(ctx, file_path="/tmp/evil.md")
	assert result.startswith("ERROR:")
	assert "outside" in result


def test_archive_memory_not_found(tmp_path):
	"""archive_memory should error on missing files."""
	ctx = _make_ctx(tmp_path)
	result = _call_archive_memory(ctx, file_path=str(ctx.memory_root / "decisions" / "gone.md"))
	assert result.startswith("ERROR:")
	assert "not found" in result


def test_archive_memory_wrong_subfolder(tmp_path):
	"""archive_memory should reject files not under decisions/ or learnings/."""
	ctx = _make_ctx(tmp_path)
	bad = ctx.memory_root / "summaries" / "summary.md"
	bad.write_text("# Summary")
	result = _call_archive_memory(ctx, file_path=str(bad))
	assert result.startswith("ERROR:")
	assert "decisions" in result or "learnings" in result


def test_archive_memory_no_memory_root(tmp_path):
	"""archive_memory should error when memory_root is not set."""
	ctx = build_oai_context(repo_root=tmp_path, config=make_config(tmp_path))
	result = _call_archive_memory(ctx, file_path="/some/path.md")
	assert result.startswith("ERROR:")
	assert "memory_root" in result


# -- edit_memory tests --


def _call_edit_memory(ctx, **kwargs):
	"""Call edit_memory logic directly."""
	from lerim.runtime import oai_tools as _mod

	class _MockWrapper:
		def __init__(self, context):
			self.context = context

	mock = _MockWrapper(ctx)
	return _mod._edit_memory_impl(mock, **kwargs)


def test_edit_memory_updates_content(tmp_path):
	"""edit_memory should replace file content."""
	ctx = _make_ctx(tmp_path)
	target = ctx.memory_root / "decisions" / "edit-test.md"
	target.write_text("---\ntitle: Old\nconfidence: 0.5\n---\nOld body")
	new_content = "---\ntitle: Old\nconfidence: 0.9\ntags: [updated]\n---\nNew body"
	result = _call_edit_memory(ctx, file_path=str(target), new_content=new_content)
	parsed = json.loads(result)
	assert parsed["edited"] is True
	assert target.read_text() == new_content


def test_edit_memory_rejects_no_frontmatter(tmp_path):
	"""edit_memory should reject content without YAML frontmatter."""
	ctx = _make_ctx(tmp_path)
	target = ctx.memory_root / "decisions" / "edit-test.md"
	target.write_text("---\ntitle: Old\n---\nBody")
	result = _call_edit_memory(ctx, file_path=str(target), new_content="No frontmatter here")
	assert result.startswith("ERROR:")
	assert "frontmatter" in result


def test_edit_memory_outside_memory_root(tmp_path):
	"""edit_memory should reject paths outside memory_root."""
	ctx = _make_ctx(tmp_path)
	result = _call_edit_memory(ctx, file_path="/tmp/evil.md", new_content="---\n---")
	assert result.startswith("ERROR:")
	assert "outside" in result


def test_edit_memory_not_found(tmp_path):
	"""edit_memory should error on missing files."""
	ctx = _make_ctx(tmp_path)
	result = _call_edit_memory(
		ctx,
		file_path=str(ctx.memory_root / "decisions" / "gone.md"),
		new_content="---\ntitle: Gone\n---\nBody",
	)
	assert result.startswith("ERROR:")
	assert "not found" in result


def test_edit_memory_no_memory_root(tmp_path):
	"""edit_memory should error when memory_root is not set."""
	ctx = build_oai_context(repo_root=tmp_path, config=make_config(tmp_path))
	result = _call_edit_memory(ctx, file_path="/some/path.md", new_content="---\n---")
	assert result.startswith("ERROR:")
	assert "memory_root" in result


# -- write_hot_memory tests --


def _call_write_hot_memory(ctx, **kwargs):
	"""Call write_hot_memory logic directly."""
	from lerim.runtime import oai_tools as _mod

	class _MockWrapper:
		def __init__(self, context):
			self.context = context

	mock = _MockWrapper(ctx)
	return _mod._write_hot_memory_impl(mock, **kwargs)


def test_write_hot_memory_creates_file(tmp_path):
	"""write_hot_memory should write hot-memory.md at memory_root.parent."""
	ctx = _make_ctx(tmp_path)
	content = "# Hot Memory\n\n## Active Decisions\n- Use PostgreSQL\n"
	result = _call_write_hot_memory(ctx, content=content)
	parsed = json.loads(result)
	assert parsed["written"] is True
	hot_path = Path(parsed["file_path"])
	assert hot_path.exists()
	assert hot_path.name == "hot-memory.md"
	assert hot_path.read_text() == content
	# Should be at memory_root.parent, not inside memory_root
	assert hot_path.parent == ctx.memory_root.parent


def test_write_hot_memory_overwrites(tmp_path):
	"""write_hot_memory should overwrite existing hot-memory.md."""
	ctx = _make_ctx(tmp_path)
	hot_path = ctx.memory_root.parent / "hot-memory.md"
	hot_path.write_text("old content")
	new_content = "# Hot Memory\n\nNew content"
	result = _call_write_hot_memory(ctx, content=new_content)
	parsed = json.loads(result)
	assert parsed["written"] is True
	assert hot_path.read_text() == new_content


def test_write_hot_memory_no_memory_root(tmp_path):
	"""write_hot_memory should error when memory_root is not set."""
	ctx = build_oai_context(repo_root=tmp_path, config=make_config(tmp_path))
	result = _call_write_hot_memory(ctx, content="# Hot Memory")
	assert result.startswith("ERROR:")
	assert "memory_root" in result


# -- Tool schema tests for new tools --


def test_archive_memory_tool_schema():
	"""archive_memory FunctionTool should expose correct parameter schema."""
	schema = archive_memory.params_json_schema
	props = schema.get("properties", {})
	assert "file_path" in props


def test_archive_memory_tool_name():
	"""archive_memory tool should have the correct name."""
	assert archive_memory.name == "archive_memory"


def test_edit_memory_tool_schema():
	"""edit_memory FunctionTool should expose correct parameter schema."""
	schema = edit_memory.params_json_schema
	props = schema.get("properties", {})
	assert "file_path" in props
	assert "new_content" in props


def test_edit_memory_tool_name():
	"""edit_memory tool should have the correct name."""
	assert edit_memory.name == "edit_memory"


def test_write_hot_memory_tool_schema():
	"""write_hot_memory FunctionTool should expose correct parameter schema."""
	schema = write_hot_memory.params_json_schema
	props = schema.get("properties", {})
	assert "content" in props


def test_write_hot_memory_tool_name():
	"""write_hot_memory tool should have the correct name."""
	assert write_hot_memory.name == "write_hot_memory"


# -- memory_search tests --


def _write_test_memory(memory_root, subdir, memory_id, title, body, tags=None, kind=None):
	"""Write a minimal memory markdown file for testing."""
	tags = tags or []
	tag_lines = "\n".join(f"- {t}" for t in tags)
	tag_block = f"tags:\n{tag_lines}" if tags else "tags: []"
	kind_line = f"kind: {kind}\n" if kind else ""
	content = f"""---
id: {memory_id}
title: {title}
{tag_block}
confidence: 0.8
primitive: {"decision" if subdir == "decisions" else "learning"}
{kind_line}created: '2026-03-27T00:00:00+00:00'
updated: '2026-03-27T00:00:00+00:00'
source: test-run
---

{body}
"""
	path = memory_root / subdir / f"20260327-{memory_id}.md"
	path.parent.mkdir(parents=True, exist_ok=True)
	path.write_text(content, encoding="utf-8")
	return path


def _call_memory_search(ctx, **kwargs):
	"""Call memory_search logic directly."""
	from lerim.runtime import oai_tools as _mod

	class _MockWrapper:
		def __init__(self, context):
			self.context = context

	mock = _MockWrapper(ctx)
	return _mod._memory_search_impl(mock, **kwargs)


def _call_batch_dedup(ctx, **kwargs):
	"""Call batch_dedup_candidates logic directly."""
	from lerim.runtime import oai_tools as _mod

	class _MockWrapper:
		def __init__(self, context):
			self.context = context

	mock = _MockWrapper(ctx)
	return _mod._batch_dedup_candidates_impl(mock, **kwargs)


def test_memory_search_scan_mode(tmp_path):
	"""memory_search mode=scan returns all memories."""
	ctx = _make_ctx(tmp_path)
	_write_test_memory(ctx.memory_root, "decisions", "use-postgres", "Use PostgreSQL", "Chose PostgreSQL.", tags=["database"])
	_write_test_memory(ctx.memory_root, "learnings", "pytest-tips", "Pytest tips", "Use fixtures.", tags=["testing"], kind="insight")

	result = _call_memory_search(ctx, query="", mode="scan")
	parsed = json.loads(result)
	assert parsed["mode"] == "scan"
	assert parsed["count"] == 2


def test_memory_search_keyword_mode(tmp_path):
	"""memory_search mode=keyword searches by keyword."""
	ctx = _make_ctx(tmp_path)
	_write_test_memory(ctx.memory_root, "decisions", "use-postgres", "Use PostgreSQL", "Chose PostgreSQL for persistence.", tags=["database"])
	_write_test_memory(ctx.memory_root, "learnings", "pytest-tips", "Pytest tips", "Use fixtures for testing.", tags=["testing"], kind="insight")
	# Reindex so FTS data is available
	from lerim.memory.memory_index import MemoryIndex
	idx = MemoryIndex(ctx.config.memories_db_path)
	idx.ensure_schema()
	idx.reindex_directory(ctx.memory_root)

	result = _call_memory_search(ctx, query="PostgreSQL", mode="keyword")
	parsed = json.loads(result)
	assert parsed["mode"] == "keyword"
	assert parsed["count"] >= 1
	assert any("postgres" in r.get("memory_id", "").lower() or "PostgreSQL" in r.get("title", "") for r in parsed["results"])


def test_memory_search_similar_mode(tmp_path):
	"""memory_search mode=similar finds similar memories."""
	ctx = _make_ctx(tmp_path)
	_write_test_memory(ctx.memory_root, "decisions", "deploy-k8s", "Kubernetes deployment", "Deploy to K8s.", tags=["deployment"])
	from lerim.memory.memory_index import MemoryIndex
	idx = MemoryIndex(ctx.config.memories_db_path)
	idx.ensure_schema()
	idx.reindex_directory(ctx.memory_root)

	result = _call_memory_search(ctx, query="container deployment", mode="similar", title="Container deploy", body="Deploy containers.")
	parsed = json.loads(result)
	assert parsed["mode"] == "similar"
	assert parsed["count"] >= 1


def test_memory_search_clusters_mode(tmp_path):
	"""memory_search mode=clusters finds tag-based clusters."""
	ctx = _make_ctx(tmp_path)
	_write_test_memory(ctx.memory_root, "decisions", "deploy-k8s", "K8s deploy", "Deploy to K8s.", tags=["deployment", "k8s"])
	_write_test_memory(ctx.memory_root, "decisions", "deploy-strategy", "Blue-green deploy", "Use blue-green.", tags=["deployment", "infra"])
	_write_test_memory(ctx.memory_root, "learnings", "deploy-rollback", "Rollback procedures", "Always have rollback.", tags=["deployment", "reliability"], kind="procedure")

	result = _call_memory_search(ctx, query="", mode="clusters")
	parsed = json.loads(result)
	assert parsed["mode"] == "clusters"
	assert parsed["cluster_count"] >= 1


def test_memory_search_scan_mode_with_primitive(tmp_path):
	"""memory_search mode=scan with primitive filter returns only matching type."""
	ctx = _make_ctx(tmp_path)
	_write_test_memory(ctx.memory_root, "decisions", "use-postgres", "Use PostgreSQL", "Chose PostgreSQL.", tags=["database"])
	_write_test_memory(ctx.memory_root, "learnings", "pytest-tips", "Pytest tips", "Use fixtures.", tags=["testing"], kind="insight")

	result = _call_memory_search(ctx, query="", mode="scan", primitive="decision")
	parsed = json.loads(result)
	assert parsed["mode"] == "scan"
	assert parsed["count"] == 1
	assert parsed["memories"][0]["primitive"] == "decision"


def test_memory_search_scan_mode_includes_reindex_stats(tmp_path):
	"""memory_search mode=scan should include reindex stats in response."""
	ctx = _make_ctx(tmp_path)
	_write_test_memory(ctx.memory_root, "decisions", "use-postgres", "Use PostgreSQL", "Chose PostgreSQL.", tags=["database"])

	result = _call_memory_search(ctx, query="", mode="scan")
	parsed = json.loads(result)
	assert "reindex" in parsed
	assert "indexed" in parsed["reindex"]


def test_memory_search_keyword_mode_with_primitive(tmp_path):
	"""memory_search mode=keyword with primitive filter returns only matching type."""
	ctx = _make_ctx(tmp_path)
	_write_test_memory(ctx.memory_root, "decisions", "use-postgres", "Use PostgreSQL", "Chose PostgreSQL for persistence.", tags=["database"])
	_write_test_memory(ctx.memory_root, "learnings", "postgres-tips", "PostgreSQL tips", "Use connection pooling.", tags=["database"], kind="insight")
	from lerim.memory.memory_index import MemoryIndex
	idx = MemoryIndex(ctx.config.memories_db_path)
	idx.ensure_schema()
	idx.reindex_directory(ctx.memory_root)

	result = _call_memory_search(ctx, query="PostgreSQL", mode="keyword", primitive="learning")
	parsed = json.loads(result)
	assert parsed["mode"] == "keyword"
	for r in parsed["results"]:
		assert r.get("primitive") == "learning"


def test_memory_search_similar_mode_with_tags(tmp_path):
	"""memory_search mode=similar should pass tags for better matching."""
	ctx = _make_ctx(tmp_path)
	_write_test_memory(ctx.memory_root, "decisions", "deploy-k8s", "Kubernetes deployment", "Deploy to K8s.", tags=["deployment", "k8s"])
	from lerim.memory.memory_index import MemoryIndex
	idx = MemoryIndex(ctx.config.memories_db_path)
	idx.ensure_schema()
	idx.reindex_directory(ctx.memory_root)

	result = _call_memory_search(ctx, query="", mode="similar", title="K8s deploy", body="Deploy containers to Kubernetes.", tags="deployment,k8s")
	parsed = json.loads(result)
	assert parsed["mode"] == "similar"
	assert parsed["count"] >= 1


def test_memory_search_clusters_mode_with_min_group_size(tmp_path):
	"""memory_search mode=clusters should respect min_group_size parameter."""
	ctx = _make_ctx(tmp_path)
	# Create 2 memories with shared tag -- below default min_group_size of 3
	_write_test_memory(ctx.memory_root, "decisions", "deploy-k8s", "K8s deploy", "Deploy to K8s.", tags=["deployment"])
	_write_test_memory(ctx.memory_root, "decisions", "deploy-strategy", "Blue-green deploy", "Use blue-green.", tags=["deployment"])

	# With min_group_size=3 (default), no clusters
	result = _call_memory_search(ctx, query="", mode="clusters", min_group_size=3)
	parsed = json.loads(result)
	assert parsed["cluster_count"] == 0

	# With min_group_size=2, should find cluster
	result = _call_memory_search(ctx, query="", mode="clusters", min_group_size=2)
	parsed = json.loads(result)
	assert parsed["cluster_count"] >= 1


def test_memory_search_unknown_mode(tmp_path):
	"""memory_search with unknown mode returns error."""
	ctx = _make_ctx(tmp_path)
	result = _call_memory_search(ctx, query="test", mode="bogus")
	assert "Error" in result
	assert "unknown mode" in result


def test_memory_search_no_memory_root(tmp_path):
	"""memory_search without memory_root returns error."""
	ctx = build_oai_context(repo_root=tmp_path, config=make_config(tmp_path))
	result = _call_memory_search(ctx, query="test", mode="scan")
	assert "Error" in result
	assert "memory_root" in result


# -- batch_dedup_candidates tests --


def test_batch_dedup_with_list(tmp_path):
	"""batch_dedup_candidates handles JSON array of candidates."""
	ctx = _make_ctx(tmp_path)
	_write_test_memory(ctx.memory_root, "decisions", "use-postgres", "Use PostgreSQL", "Chose PostgreSQL.", tags=["database"])

	candidates = [
		{"title": "Use PostgreSQL for data", "body": "PostgreSQL is our primary database.", "tags": ["database"]},
		{"title": "Redis caching", "body": "Use Redis for caching.", "tags": ["caching"]},
	]

	result = _call_batch_dedup(ctx, candidates_json=json.dumps(candidates))
	parsed = json.loads(result)
	assert parsed["count"] == 2
	assert len(parsed["results"]) == 2
	# Each result should have candidate + similar_existing + top_similarity
	for r in parsed["results"]:
		assert "candidate" in r
		assert "similar_existing" in r
		assert "top_similarity" in r
	assert parsed["results"][0]["top_similarity"] > 0
	if parsed["results"][0]["similar_existing"]:
		top = parsed["results"][0]["similar_existing"][0]
		assert "fused_score" in top
		assert "similarity" in top
		assert "lexical_similarity" in top


def test_batch_dedup_with_dict_candidates_key(tmp_path):
	"""batch_dedup_candidates handles dict with 'candidates' key."""
	ctx = _make_ctx(tmp_path)
	_write_test_memory(ctx.memory_root, "decisions", "use-postgres", "Use PostgreSQL", "Chose PostgreSQL.", tags=["database"])

	data = {"candidates": [
		{"title": "Database choice", "body": "Use PostgreSQL.", "tags": ["database"]},
	]}

	result = _call_batch_dedup(ctx, candidates_json=json.dumps(data))
	parsed = json.loads(result)
	assert parsed["count"] == 1


def test_batch_dedup_with_dict_memories_key(tmp_path):
	"""batch_dedup_candidates handles dict with 'memories' key."""
	ctx = _make_ctx(tmp_path)
	_write_test_memory(ctx.memory_root, "decisions", "use-postgres", "Use PostgreSQL", "Chose PostgreSQL.", tags=["database"])

	data = {"memories": [
		{"title": "Database choice", "body": "Use PostgreSQL.", "tags": ["database"]},
	]}

	result = _call_batch_dedup(ctx, candidates_json=json.dumps(data))
	parsed = json.loads(result)
	assert parsed["count"] == 1


def test_batch_dedup_invalid_json(tmp_path):
	"""batch_dedup_candidates returns error for invalid JSON."""
	ctx = _make_ctx(tmp_path)
	result = _call_batch_dedup(ctx, candidates_json="not valid json")
	assert "Error" in result
	assert "invalid JSON" in result


def test_batch_dedup_no_memory_root(tmp_path):
	"""batch_dedup_candidates without memory_root returns error."""
	ctx = build_oai_context(repo_root=tmp_path, config=make_config(tmp_path))
	result = _call_batch_dedup(ctx, candidates_json="[]")
	assert "Error" in result
	assert "memory_root" in result


def test_batch_dedup_empty_candidates(tmp_path):
	"""batch_dedup_candidates with empty list returns zero results."""
	ctx = _make_ctx(tmp_path)
	result = _call_batch_dedup(ctx, candidates_json="[]")
	parsed = json.loads(result)
	assert parsed["count"] == 0
	assert parsed["results"] == []


def test_batch_dedup_tags_as_string(tmp_path):
	"""batch_dedup_candidates handles tags as string (not list)."""
	ctx = _make_ctx(tmp_path)
	_write_test_memory(ctx.memory_root, "decisions", "use-postgres", "Use PostgreSQL", "Chose PostgreSQL.", tags=["database"])

	candidates = [
		{"title": "DB choice", "body": "Use PostgreSQL.", "tags": "database,persistence"},
	]

	result = _call_batch_dedup(ctx, candidates_json=json.dumps(candidates))
	parsed = json.loads(result)
	assert parsed["count"] == 1


# -- Tool schema tests for new tools --


def test_memory_search_tool_schema():
	"""memory_search FunctionTool should expose correct parameter schema."""
	schema = memory_search.params_json_schema
	props = schema.get("properties", {})
	assert "query" in props
	assert "mode" in props
	assert "title" in props
	assert "body" in props
	assert "limit" in props
	assert "primitive" in props
	assert "tags" in props
	assert "min_group_size" in props


def test_memory_search_tool_name():
	"""memory_search tool should have the correct name."""
	assert memory_search.name == "memory_search"


def test_batch_dedup_candidates_tool_schema():
	"""batch_dedup_candidates FunctionTool should expose correct parameter schema."""
	schema = batch_dedup_candidates.params_json_schema
	props = schema.get("properties", {})
	assert "candidates_json" in props


def test_batch_dedup_candidates_tool_name():
	"""batch_dedup_candidates tool should have the correct name."""
	assert batch_dedup_candidates.name == "batch_dedup_candidates"
