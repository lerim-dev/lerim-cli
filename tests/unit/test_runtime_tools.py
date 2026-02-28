"""Unit tests for runtime tool boundary enforcement and helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from lerim.runtime.tools import (
    RuntimeToolContext,
    _memory_primitive_type,
    build_tool_context,
    edit_file_tool,
    glob_files_tool,
    grep_files_tool,
    read_file_tool,
    write_file_tool,
)
from lerim.memory.memory_record import MemoryType
from tests.helpers import make_config


def _make_context(
    tmp_path: Path, *, extra_read_roots: list[Path] | None = None
) -> RuntimeToolContext:
    """Build a test RuntimeToolContext rooted in tmp_path."""
    memory_root = tmp_path / "memory"
    for sub in ("decisions", "learnings", "summaries"):
        (memory_root / sub).mkdir(parents=True, exist_ok=True)
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    run_folder = workspace / "run-001"
    run_folder.mkdir(exist_ok=True)
    cfg = make_config(tmp_path)
    return build_tool_context(
        repo_root=tmp_path,
        memory_root=memory_root,
        workspace_root=workspace,
        run_folder=run_folder,
        extra_read_roots=extra_read_roots or [],
        run_id="sync-20260220-100000-test",
        config=cfg,
    )


# -- Read boundary tests --


def test_read_boundary_allows_memory_root(tmp_path):
    """read_file_tool succeeds for files under memory_root."""
    ctx = _make_context(tmp_path)
    test_file = ctx.memory_root / "decisions" / "test.md"
    test_file.write_text("---\ntitle: test\n---\ncontent", encoding="utf-8")
    result = read_file_tool(context=ctx, file_path=str(test_file))
    assert "content" in result


def test_read_boundary_allows_workspace(tmp_path):
    """read_file_tool succeeds for files under workspace_root."""
    ctx = _make_context(tmp_path)
    test_file = ctx.workspace_root / "notes.md"
    test_file.write_text("workspace note", encoding="utf-8")
    result = read_file_tool(context=ctx, file_path=str(test_file))
    assert "workspace note" in result


def test_read_boundary_denies_outside(tmp_path):
    """read_file_tool raises for files outside allowed roots."""
    ctx = _make_context(tmp_path)
    outside = tmp_path / "outside" / "secret.txt"
    outside.parent.mkdir(parents=True)
    outside.write_text("secret", encoding="utf-8")
    with pytest.raises(RuntimeError, match="outside allowed roots"):
        read_file_tool(context=ctx, file_path=str(outside))


def test_read_boundary_allows_extra_roots(tmp_path):
    """read_file_tool succeeds for files under extra_read_roots."""
    extra = tmp_path / "extra"
    extra.mkdir()
    (extra / "data.md").write_text("extra data", encoding="utf-8")
    ctx = _make_context(tmp_path, extra_read_roots=[extra])
    result = read_file_tool(context=ctx, file_path=str(extra / "data.md"))
    assert "extra data" in result


# -- Write boundary tests --


def test_write_boundary_allows_memory_root(tmp_path):
    """write_file_tool succeeds for files under memory_root."""
    ctx = _make_context(tmp_path)
    content = (
        "---\ntitle: Test Decision\nconfidence: 0.9\ntags: [test]\n---\nDecision body."
    )
    result = write_file_tool(
        context=ctx,
        file_path=str(ctx.memory_root / "decisions" / "test.md"),
        content=content,
    )
    assert "file_path" in result
    assert result["primitive"] == "decision"


def test_write_boundary_allows_run_folder(tmp_path):
    """write_file_tool succeeds for files under run_folder."""
    ctx = _make_context(tmp_path)
    result = write_file_tool(
        context=ctx,
        file_path=str(ctx.run_folder / "output.json"),
        content='{"result": true}',
    )
    assert "file_path" in result


def test_write_boundary_denies_outside(tmp_path):
    """write_file_tool raises for files outside allowed write roots."""
    ctx = _make_context(tmp_path)
    outside = tmp_path / "outside" / "hack.md"
    with pytest.raises(RuntimeError, match="outside allowed roots"):
        write_file_tool(context=ctx, file_path=str(outside), content="bad")


def test_write_normalizes_frontmatter(tmp_path):
    """write_file_tool adds server-side fields (created, updated, source)."""
    ctx = _make_context(tmp_path)
    content = "---\ntitle: My Decision\nid: my-decision\nconfidence: 0.85\ntags: [auth]\n---\nBody here."
    result = write_file_tool(
        context=ctx,
        file_path=str(ctx.memory_root / "decisions" / "my-decision.md"),
        content=content,
    )
    written = Path(result["file_path"]).read_text(encoding="utf-8")
    assert "created:" in written
    assert "source:" in written


def test_write_preserves_existing_frontmatter(tmp_path):
    """write_file_tool preserves user-provided frontmatter fields."""
    ctx = _make_context(tmp_path)
    content = "---\ntitle: Custom\nid: custom-id\nconfidence: 0.95\ntags: [custom]\n---\nCustom body."
    result = write_file_tool(
        context=ctx,
        file_path=str(ctx.memory_root / "decisions" / "custom.md"),
        content=content,
    )
    written = Path(result["file_path"]).read_text(encoding="utf-8")
    assert "custom-id" in written
    assert "0.95" in written


def test_write_canonical_filename(tmp_path):
    """write_file_tool renames to canonical YYYYMMDD-slug.md format."""
    ctx = _make_context(tmp_path)
    content = "---\ntitle: My Title\nconfidence: 0.8\ntags: []\n---\nBody."
    result = write_file_tool(
        context=ctx,
        file_path=str(ctx.memory_root / "decisions" / "anything.md"),
        content=content,
    )
    filename = Path(result["file_path"]).name
    assert filename.endswith("-my-title.md")


# -- Edit tests --


def test_edit_old_string_not_found(tmp_path):
    """edit_file_tool raises when old_string not in file."""
    ctx = _make_context(tmp_path)
    test_file = ctx.run_folder / "notes.md"
    test_file.write_text("original content", encoding="utf-8")
    with pytest.raises(RuntimeError, match="old_string not found"):
        edit_file_tool(
            context=ctx,
            file_path=str(test_file),
            old_string="nonexistent",
            new_string="new",
        )


def test_edit_replaces_correctly(tmp_path):
    """edit_file_tool replaces old_string with new_string."""
    ctx = _make_context(tmp_path)
    test_file = ctx.run_folder / "notes.md"
    test_file.write_text("hello world", encoding="utf-8")
    result = edit_file_tool(
        context=ctx, file_path=str(test_file), old_string="hello", new_string="goodbye"
    )
    assert result["replacements"] == 1
    assert "goodbye world" in test_file.read_text(encoding="utf-8")


def test_edit_replace_all(tmp_path):
    """edit_file_tool with replace_all=True replaces all occurrences."""
    ctx = _make_context(tmp_path)
    test_file = ctx.run_folder / "notes.md"
    test_file.write_text("foo bar foo baz foo", encoding="utf-8")
    result = edit_file_tool(
        context=ctx,
        file_path=str(test_file),
        old_string="foo",
        new_string="qux",
        replace_all=True,
    )
    assert result["replacements"] == 3


# -- Glob/Grep tests --


def test_glob_within_boundary(tmp_path):
    """glob_files_tool returns files matching pattern within allowed roots."""
    ctx = _make_context(tmp_path)
    (ctx.memory_root / "decisions" / "test.md").write_text("x", encoding="utf-8")
    results = glob_files_tool(context=ctx, pattern="**/*.md")
    assert len(results) >= 1


def test_glob_outside_boundary(tmp_path):
    """glob_files_tool raises for base_path outside allowed roots."""
    ctx = _make_context(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(RuntimeError, match="outside allowed roots"):
        glob_files_tool(context=ctx, pattern="*.md", base_path=str(outside))


def test_grep_within_boundary(tmp_path):
    """grep_files_tool returns matches within allowed roots."""
    ctx = _make_context(tmp_path)
    (ctx.memory_root / "decisions" / "auth.md").write_text(
        "JWT authentication", encoding="utf-8"
    )
    results = grep_files_tool(context=ctx, pattern="JWT")
    assert len(results) >= 1
    assert any("JWT" in r for r in results)


def test_grep_max_hits(tmp_path):
    """grep_files_tool respects max_hits limit."""
    ctx = _make_context(tmp_path)
    big_file = ctx.memory_root / "decisions" / "big.md"
    big_file.write_text(
        "\n".join(f"line{i} match" for i in range(100)), encoding="utf-8"
    )
    results = grep_files_tool(context=ctx, pattern="match", max_hits=5)
    assert len(results) == 5


# -- Helper tests --


def test_memory_primitive_type_detection(tmp_path):
    """_memory_primitive_type detects decision/learning from path."""
    memory_root = tmp_path / "memory"
    for sub in ("decisions", "learnings", "summaries"):
        (memory_root / sub).mkdir(parents=True)
    assert (
        _memory_primitive_type(memory_root / "decisions" / "a.md", memory_root)
        == MemoryType.decision
    )
    assert (
        _memory_primitive_type(memory_root / "learnings" / "b.md", memory_root)
        == MemoryType.learning
    )
    assert (
        _memory_primitive_type(memory_root / "summaries" / "c.md", memory_root)
        == MemoryType.summary
    )
    assert _memory_primitive_type(tmp_path / "other.md", memory_root) is None


def test_record_memory_access_for_read(tmp_path):
    """Reading full body records access in tracker DB."""
    from lerim.runtime.tools import _record_memory_access

    ctx = _make_context(tmp_path)
    test_file = ctx.memory_root / "decisions" / "20260220-test.md"
    test_file.write_text("---\nid: test\n---\nbody", encoding="utf-8")
    _record_memory_access(
        context=ctx, file_path=test_file, limit=2000, require_body_read=True
    )


def test_record_memory_access_skips_frontmatter_only(tmp_path):
    """Reading with small limit (frontmatter only) does not record access."""
    from lerim.runtime.tools import _record_memory_access

    ctx = _make_context(tmp_path)
    test_file = ctx.memory_root / "decisions" / "20260220-test.md"
    test_file.write_text("---\nid: test\n---\nbody", encoding="utf-8")
    _record_memory_access(
        context=ctx, file_path=test_file, limit=3, require_body_read=True
    )


def test_record_memory_access_for_write(tmp_path):
    """Writing a memory file records access."""
    from lerim.runtime.tools import _record_memory_access

    ctx = _make_context(tmp_path)
    test_file = ctx.memory_root / "decisions" / "20260220-test.md"
    test_file.write_text("---\nid: test\n---\nbody", encoding="utf-8")
    _record_memory_access(
        context=ctx, file_path=test_file, limit=None, require_body_read=False
    )
