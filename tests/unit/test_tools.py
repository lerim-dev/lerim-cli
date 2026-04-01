"""Unit tests for DSPy ReAct tools (write_memory, write_report, read_file, list_files,
archive_memory, edit_memory, write_hot_memory, memory_search, batch_dedup_candidates,
bind_sync_tools, bind_maintain_tools, bind_ask_tools)."""

from __future__ import annotations

import json
from functools import partial
from pathlib import Path

import pytest

from lerim.runtime.context import RuntimeContext, build_context
from lerim.runtime.tools import (
	archive_memory,
	batch_dedup_candidates,
	bind_ask_tools,
	bind_maintain_tools,
	bind_sync_tools,
	edit_memory,
	list_files,
	memory_search,
	read_file,
	write_hot_memory,
	write_memory,
	write_report,
)
from tests.helpers import make_config


def _make_ctx(tmp_path: Path, **overrides) -> RuntimeContext:
	"""Build a RuntimeContext for testing."""
	mem_root = tmp_path / "memories"
	mem_root.mkdir(exist_ok=True)
	(mem_root / "decisions").mkdir(exist_ok=True)
	(mem_root / "learnings").mkdir(exist_ok=True)
	(mem_root / "summaries").mkdir(exist_ok=True)
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


def test_write_memory_valid_decision(tmp_path):
	"""Valid decision memory should write a file and return JSON with file_path."""
	ctx = _make_ctx(tmp_path)
	result = write_memory(
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
	result = write_memory(
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
	result = write_memory(
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
	result = write_memory(
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
	result = write_memory(
		ctx,
		primitive="decision",
		title="Default confidence test",
		body="Should default to 0.8.",
	)
	parsed = json.loads(result)
	content = Path(parsed["file_path"]).read_text()
	assert "confidence: 0.8" in content


# ---------------------------------------------------------------------------
# write_memory: validation errors
# ---------------------------------------------------------------------------


def test_write_memory_invalid_primitive(tmp_path):
	"""Invalid primitive should return an ERROR string."""
	ctx = _make_ctx(tmp_path)
	result = write_memory(
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
	result = write_memory(
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
	result = write_memory(
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
	result = write_memory(
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
	result = write_memory(
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
	result = write_memory(
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
	result = write_memory(
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
	ctx = build_context(
		repo_root=tmp_path,
		config=make_config(tmp_path),
	)
	result = write_memory(
		ctx,
		primitive="decision",
		title="No root",
		body="Should fail.",
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
	test_file = ctx.memory_root / "decisions" / "test.md"
	test_file.write_text("# Test Decision\nSome content.")
	result = read_file(ctx, file_path=str(test_file))
	assert "Test Decision" in result


def test_read_file_from_run_folder(tmp_path):
	"""read_file should read files within run_folder."""
	ctx = _make_ctx(tmp_path)
	test_file = ctx.run_folder / "extract.json"
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
	(ctx.memory_root / "decisions" / "a.md").write_text("# A")
	(ctx.memory_root / "decisions" / "b.md").write_text("# B")
	result = list_files(ctx, directory=str(ctx.memory_root / "decisions"))
	files = json.loads(result)
	assert len(files) == 2


def test_list_files_empty_dir(tmp_path):
	"""list_files should return empty list for empty directory."""
	ctx = _make_ctx(tmp_path)
	result = list_files(ctx, directory=str(ctx.memory_root / "decisions"))
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
	(ctx.memory_root / "decisions" / "a.md").write_text("# A")
	(ctx.memory_root / "decisions" / "b.json").write_text("{}")
	result = list_files(
		ctx,
		directory=str(ctx.memory_root / "decisions"),
		pattern="*.json",
	)
	files = json.loads(result)
	assert len(files) == 1
	assert files[0].endswith(".json")


# ---------------------------------------------------------------------------
# archive_memory tests
# ---------------------------------------------------------------------------


def test_archive_memory_decision(tmp_path):
	"""archive_memory should move a decision file to archived/decisions/."""
	ctx = _make_ctx(tmp_path)
	src = ctx.memory_root / "decisions" / "test-decision.md"
	src.write_text("---\ntitle: Test\n---\nBody")
	result = archive_memory(ctx, file_path=str(src))
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
	result = archive_memory(ctx, file_path=str(src))
	parsed = json.loads(result)
	assert parsed["archived"] is True
	assert not src.exists()
	target = Path(parsed["target"])
	assert target.exists()
	assert "archived/learnings/test-learning.md" in str(target)


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
		file_path=str(ctx.memory_root / "decisions" / "gone.md"),
	)
	assert result.startswith("ERROR:")
	assert "not found" in result


def test_archive_memory_wrong_subfolder(tmp_path):
	"""archive_memory should reject files not under decisions/ or learnings/."""
	ctx = _make_ctx(tmp_path)
	bad = ctx.memory_root / "summaries" / "summary.md"
	bad.write_text("# Summary")
	result = archive_memory(ctx, file_path=str(bad))
	assert result.startswith("ERROR:")
	assert "decisions" in result or "learnings" in result


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
	target = ctx.memory_root / "decisions" / "edit-test.md"
	target.write_text("---\ntitle: Old\nconfidence: 0.5\n---\nOld body")
	new_content = "---\ntitle: Old\nconfidence: 0.9\ntags: [updated]\n---\nNew body"
	result = edit_memory(ctx, file_path=str(target), new_content=new_content)
	parsed = json.loads(result)
	assert parsed["edited"] is True
	assert target.read_text() == new_content


def test_edit_memory_rejects_no_frontmatter(tmp_path):
	"""edit_memory should reject content without YAML frontmatter."""
	ctx = _make_ctx(tmp_path)
	target = ctx.memory_root / "decisions" / "edit-test.md"
	target.write_text("---\ntitle: Old\n---\nBody")
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
		file_path=str(ctx.memory_root / "decisions" / "gone.md"),
		new_content="---\ntitle: Gone\n---\nBody",
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
# write_hot_memory tests
# ---------------------------------------------------------------------------


def test_write_hot_memory_creates_file(tmp_path):
	"""write_hot_memory should write hot-memory.md at memory_root.parent."""
	ctx = _make_ctx(tmp_path)
	content = "# Hot Memory\n\n## Active Decisions\n- Use PostgreSQL\n"
	result = write_hot_memory(ctx, content=content)
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
	result = write_hot_memory(ctx, content=new_content)
	parsed = json.loads(result)
	assert parsed["written"] is True
	assert hot_path.read_text() == new_content


def test_write_hot_memory_no_memory_root(tmp_path):
	"""write_hot_memory should error when memory_root is not set."""
	ctx = build_context(repo_root=tmp_path, config=make_config(tmp_path))
	result = write_hot_memory(ctx, content="# Hot Memory")
	assert result.startswith("ERROR:")
	assert "memory_root" in result


# ---------------------------------------------------------------------------
# memory_search tests
# ---------------------------------------------------------------------------


def _write_test_memory(
	memory_root, subdir, memory_id, title, body, tags=None, kind=None,
):
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


def test_memory_search_scan_mode(tmp_path):
	"""memory_search mode=scan returns all memories."""
	ctx = _make_ctx(tmp_path)
	_write_test_memory(
		ctx.memory_root, "decisions", "use-postgres",
		"Use PostgreSQL", "Chose PostgreSQL.", tags=["database"],
	)
	_write_test_memory(
		ctx.memory_root, "learnings", "pytest-tips",
		"Pytest tips", "Use fixtures.", tags=["testing"], kind="insight",
	)

	result = memory_search(ctx, query="", mode="scan")
	parsed = json.loads(result)
	assert parsed["mode"] == "scan"
	assert parsed["count"] == 2


def test_memory_search_keyword_mode(tmp_path):
	"""memory_search mode=keyword searches by keyword."""
	ctx = _make_ctx(tmp_path)
	_write_test_memory(
		ctx.memory_root, "decisions", "use-postgres",
		"Use PostgreSQL", "Chose PostgreSQL for persistence.",
		tags=["database"],
	)
	_write_test_memory(
		ctx.memory_root, "learnings", "pytest-tips",
		"Pytest tips", "Use fixtures for testing.",
		tags=["testing"], kind="insight",
	)
	# Reindex so FTS data is available
	from lerim.memory.memory_index import MemoryIndex
	idx = MemoryIndex(ctx.config.memories_db_path)
	idx.ensure_schema()
	idx.reindex_directory(ctx.memory_root)

	result = memory_search(ctx, query="PostgreSQL", mode="keyword")
	parsed = json.loads(result)
	assert parsed["mode"] == "keyword"
	assert parsed["count"] >= 1
	assert any(
		"postgres" in r.get("memory_id", "").lower()
		or "PostgreSQL" in r.get("title", "")
		for r in parsed["results"]
	)


def test_memory_search_similar_mode(tmp_path):
	"""memory_search mode=similar finds similar memories."""
	ctx = _make_ctx(tmp_path)
	_write_test_memory(
		ctx.memory_root, "decisions", "deploy-k8s",
		"Kubernetes deployment", "Deploy to K8s.",
		tags=["deployment"],
	)
	from lerim.memory.memory_index import MemoryIndex
	idx = MemoryIndex(ctx.config.memories_db_path)
	idx.ensure_schema()
	idx.reindex_directory(ctx.memory_root)

	result = memory_search(
		ctx, query="container deployment", mode="similar",
		title="Container deploy", body="Deploy containers.",
	)
	parsed = json.loads(result)
	assert parsed["mode"] == "similar"
	assert parsed["count"] >= 1


def test_memory_search_clusters_mode(tmp_path):
	"""memory_search mode=clusters finds tag-based clusters."""
	ctx = _make_ctx(tmp_path)
	_write_test_memory(
		ctx.memory_root, "decisions", "deploy-k8s",
		"K8s deploy", "Deploy to K8s.",
		tags=["deployment", "k8s"],
	)
	_write_test_memory(
		ctx.memory_root, "decisions", "deploy-strategy",
		"Blue-green deploy", "Use blue-green.",
		tags=["deployment", "infra"],
	)
	_write_test_memory(
		ctx.memory_root, "learnings", "deploy-rollback",
		"Rollback procedures", "Always have rollback.",
		tags=["deployment", "reliability"], kind="procedure",
	)

	result = memory_search(ctx, query="", mode="clusters")
	parsed = json.loads(result)
	assert parsed["mode"] == "clusters"
	assert parsed["cluster_count"] >= 1


def test_memory_search_scan_mode_with_primitive(tmp_path):
	"""memory_search mode=scan with primitive filter returns only matching type."""
	ctx = _make_ctx(tmp_path)
	_write_test_memory(
		ctx.memory_root, "decisions", "use-postgres",
		"Use PostgreSQL", "Chose PostgreSQL.", tags=["database"],
	)
	_write_test_memory(
		ctx.memory_root, "learnings", "pytest-tips",
		"Pytest tips", "Use fixtures.", tags=["testing"], kind="insight",
	)

	result = memory_search(ctx, query="", mode="scan", primitive="decision")
	parsed = json.loads(result)
	assert parsed["mode"] == "scan"
	assert parsed["count"] == 1
	assert parsed["memories"][0]["primitive"] == "decision"


def test_memory_search_scan_mode_includes_reindex_stats(tmp_path):
	"""memory_search mode=scan should include reindex stats in response."""
	ctx = _make_ctx(tmp_path)
	_write_test_memory(
		ctx.memory_root, "decisions", "use-postgres",
		"Use PostgreSQL", "Chose PostgreSQL.", tags=["database"],
	)

	result = memory_search(ctx, query="", mode="scan")
	parsed = json.loads(result)
	assert "reindex" in parsed
	assert "indexed" in parsed["reindex"]


def test_memory_search_keyword_mode_with_primitive(tmp_path):
	"""memory_search mode=keyword with primitive filter returns only matching type."""
	ctx = _make_ctx(tmp_path)
	_write_test_memory(
		ctx.memory_root, "decisions", "use-postgres",
		"Use PostgreSQL", "Chose PostgreSQL for persistence.",
		tags=["database"],
	)
	_write_test_memory(
		ctx.memory_root, "learnings", "postgres-tips",
		"PostgreSQL tips", "Use connection pooling.",
		tags=["database"], kind="insight",
	)
	from lerim.memory.memory_index import MemoryIndex
	idx = MemoryIndex(ctx.config.memories_db_path)
	idx.ensure_schema()
	idx.reindex_directory(ctx.memory_root)

	result = memory_search(
		ctx, query="PostgreSQL", mode="keyword", primitive="learning",
	)
	parsed = json.loads(result)
	assert parsed["mode"] == "keyword"
	for r in parsed["results"]:
		assert r.get("primitive") == "learning"


def test_memory_search_similar_mode_with_tags(tmp_path):
	"""memory_search mode=similar should pass tags for better matching."""
	ctx = _make_ctx(tmp_path)
	_write_test_memory(
		ctx.memory_root, "decisions", "deploy-k8s",
		"Kubernetes deployment", "Deploy to K8s.",
		tags=["deployment", "k8s"],
	)
	from lerim.memory.memory_index import MemoryIndex
	idx = MemoryIndex(ctx.config.memories_db_path)
	idx.ensure_schema()
	idx.reindex_directory(ctx.memory_root)

	result = memory_search(
		ctx, query="", mode="similar",
		title="K8s deploy", body="Deploy containers to Kubernetes.",
		tags="deployment,k8s",
	)
	parsed = json.loads(result)
	assert parsed["mode"] == "similar"
	assert parsed["count"] >= 1


def test_memory_search_clusters_mode_with_min_group_size(tmp_path):
	"""memory_search mode=clusters should respect min_group_size parameter."""
	ctx = _make_ctx(tmp_path)
	# Create 2 memories with shared tag -- below default min_group_size of 3
	_write_test_memory(
		ctx.memory_root, "decisions", "deploy-k8s",
		"K8s deploy", "Deploy to K8s.", tags=["deployment"],
	)
	_write_test_memory(
		ctx.memory_root, "decisions", "deploy-strategy",
		"Blue-green deploy", "Use blue-green.", tags=["deployment"],
	)

	# With min_group_size=3 (default), no clusters
	result = memory_search(ctx, query="", mode="clusters", min_group_size=3)
	parsed = json.loads(result)
	assert parsed["cluster_count"] == 0

	# With min_group_size=2, should find cluster
	result = memory_search(ctx, query="", mode="clusters", min_group_size=2)
	parsed = json.loads(result)
	assert parsed["cluster_count"] >= 1


def test_memory_search_unknown_mode(tmp_path):
	"""memory_search with unknown mode returns error."""
	ctx = _make_ctx(tmp_path)
	result = memory_search(ctx, query="test", mode="bogus")
	assert "Error" in result
	assert "unknown mode" in result


def test_memory_search_no_memory_root(tmp_path):
	"""memory_search without memory_root returns error."""
	ctx = build_context(repo_root=tmp_path, config=make_config(tmp_path))
	result = memory_search(ctx, query="test", mode="scan")
	assert "Error" in result
	assert "memory_root" in result


# ---------------------------------------------------------------------------
# batch_dedup_candidates tests
# ---------------------------------------------------------------------------


def test_batch_dedup_with_list(tmp_path):
	"""batch_dedup_candidates handles JSON array of candidates."""
	ctx = _make_ctx(tmp_path)
	_write_test_memory(
		ctx.memory_root, "decisions", "use-postgres",
		"Use PostgreSQL", "Chose PostgreSQL.", tags=["database"],
	)

	candidates = [
		{
			"title": "Use PostgreSQL for data",
			"body": "PostgreSQL is our primary database.",
			"tags": ["database"],
		},
		{
			"title": "Redis caching",
			"body": "Use Redis for caching.",
			"tags": ["caching"],
		},
	]

	result = batch_dedup_candidates(
		ctx, candidates_json=json.dumps(candidates),
	)
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
	_write_test_memory(
		ctx.memory_root, "decisions", "use-postgres",
		"Use PostgreSQL", "Chose PostgreSQL.", tags=["database"],
	)

	data = {"candidates": [
		{
			"title": "Database choice",
			"body": "Use PostgreSQL.",
			"tags": ["database"],
		},
	]}

	result = batch_dedup_candidates(
		ctx, candidates_json=json.dumps(data),
	)
	parsed = json.loads(result)
	assert parsed["count"] == 1


def test_batch_dedup_with_dict_memories_key(tmp_path):
	"""batch_dedup_candidates handles dict with 'memories' key."""
	ctx = _make_ctx(tmp_path)
	_write_test_memory(
		ctx.memory_root, "decisions", "use-postgres",
		"Use PostgreSQL", "Chose PostgreSQL.", tags=["database"],
	)

	data = {"memories": [
		{
			"title": "Database choice",
			"body": "Use PostgreSQL.",
			"tags": ["database"],
		},
	]}

	result = batch_dedup_candidates(
		ctx, candidates_json=json.dumps(data),
	)
	parsed = json.loads(result)
	assert parsed["count"] == 1


def test_batch_dedup_invalid_json(tmp_path):
	"""batch_dedup_candidates returns error for invalid JSON."""
	ctx = _make_ctx(tmp_path)
	result = batch_dedup_candidates(ctx, candidates_json="not valid json")
	assert "Error" in result
	assert "invalid JSON" in result


def test_batch_dedup_no_memory_root(tmp_path):
	"""batch_dedup_candidates without memory_root returns error."""
	ctx = build_context(repo_root=tmp_path, config=make_config(tmp_path))
	result = batch_dedup_candidates(ctx, candidates_json="[]")
	assert "Error" in result
	assert "memory_root" in result


def test_batch_dedup_empty_candidates(tmp_path):
	"""batch_dedup_candidates with empty list returns zero results."""
	ctx = _make_ctx(tmp_path)
	result = batch_dedup_candidates(ctx, candidates_json="[]")
	parsed = json.loads(result)
	assert parsed["count"] == 0
	assert parsed["results"] == []


def test_batch_dedup_tags_as_string(tmp_path):
	"""batch_dedup_candidates handles tags as string (not list)."""
	ctx = _make_ctx(tmp_path)
	_write_test_memory(
		ctx.memory_root, "decisions", "use-postgres",
		"Use PostgreSQL", "Chose PostgreSQL.", tags=["database"],
	)

	candidates = [
		{
			"title": "DB choice",
			"body": "Use PostgreSQL.",
			"tags": "database,persistence",
		},
	]

	result = batch_dedup_candidates(
		ctx, candidates_json=json.dumps(candidates),
	)
	parsed = json.loads(result)
	assert parsed["count"] == 1


# ---------------------------------------------------------------------------
# Bind helper tests
# ---------------------------------------------------------------------------


def test_partial_preserves_function_name(tmp_path):
	"""Partial-bound tools should preserve the original function name."""
	ctx = _make_ctx(tmp_path)
	bound = partial(write_memory, ctx)
	assert bound.func.__name__ == "write_memory"

	bound_search = partial(memory_search, ctx)
	assert bound_search.func.__name__ == "memory_search"


@pytest.mark.parametrize("bind_fn,expected_count", [
	(bind_sync_tools, 7),
	(bind_maintain_tools, 8),
	(bind_ask_tools, 3),
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
	tools = bind_sync_tools(ctx)
	expected_names = {
		"extract_pipeline", "summarize_pipeline", "write_memory",
		"write_report", "read_file", "list_files", "batch_dedup_candidates",
	}
	seen_names = set()
	for tool in tools:
		dt = dspy.Tool(tool)
		seen_names.add(dt.name)
		assert dt.name != "partial", f"dspy.Tool name should not be 'partial'"
		assert "args" not in dt.args or len(dt.args) > 1, f"{dt.name}: args should not be generic"
	assert seen_names == expected_names
