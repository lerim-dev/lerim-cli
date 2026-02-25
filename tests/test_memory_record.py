"""Unit tests for memory taxonomy, record model, and markdown helpers."""

from __future__ import annotations

import frontmatter

from lerim.memory.memory_record import (
    MEMORY_FRONTMATTER_SCHEMA,
    MEMORY_TYPE_FOLDERS,
    MemoryRecord,
    MemoryType,
    canonical_memory_filename,
    memory_write_schema_prompt,
    slugify,
)


def test_slugify_normal():
    """Normal title -> lowercase hyphenated slug."""
    assert slugify("Hello World") == "hello-world"


def test_slugify_special_chars():
    """Special characters stripped, spaces become hyphens."""
    assert slugify("Use JWT (HS256)!") == "use-jwt-hs256"


def test_slugify_unicode():
    """Unicode chars transliterated or stripped."""
    result = slugify("café résumé")
    assert result  # non-empty
    assert "caf" in result  # accent stripped


def test_slugify_empty():
    """Empty string -> 'memory' fallback."""
    assert slugify("") == "memory"
    assert slugify("   ") == "memory"


def test_slugify_long():
    """Very long title still produces a valid slug."""
    long_title = "A" * 500
    result = slugify(long_title)
    assert len(result) > 0
    assert result.isascii()


def test_canonical_memory_filename():
    """canonical_memory_filename produces YYYYMMDD-slug.md format."""
    fname = canonical_memory_filename(
        title="My Title", run_id="sync-20260220-120000-abc123"
    )
    assert fname == "20260220-my-title.md"


def test_canonical_memory_filename_with_run_id_date():
    """When run_id contains a date, it's used for the prefix."""
    fname = canonical_memory_filename(
        title="Test", run_id="sync-20260115-093000-def456"
    )
    assert fname.startswith("20260115-")
    assert fname.endswith(".md")


def test_memory_record_to_markdown_roundtrip():
    """MemoryRecord -> to_markdown() -> parse with python-frontmatter -> same fields."""
    record = MemoryRecord(
        id="test-record",
        primitive="learning",
        kind="pitfall",
        title="Test Record",
        body="This is the body content.",
        confidence=0.75,
        tags=["test", "demo"],
        source="test-run",
    )
    md = record.to_markdown()
    parsed = frontmatter.loads(md)
    assert parsed["id"] == "test-record"
    assert parsed["title"] == "Test Record"
    assert parsed["kind"] == "pitfall"
    assert parsed["confidence"] == 0.75
    assert parsed["tags"] == ["test", "demo"]
    assert parsed.content.strip() == "This is the body content."


def test_memory_record_to_frontmatter_dict():
    """to_frontmatter_dict() has expected keys, no extra keys."""
    record = MemoryRecord(
        id="dec-1",
        primitive="decision",
        title="Decision Test",
        body="Body text",
        confidence=0.9,
        tags=["a"],
        source="run-1",
    )
    fm = record.to_frontmatter_dict()
    expected_keys = {
        "id",
        "title",
        "created",
        "updated",
        "source",
        "confidence",
        "tags",
    }
    assert set(fm.keys()) == expected_keys
    # Learning should also have 'kind'
    learning = MemoryRecord(
        id="learn-1",
        primitive="learning",
        kind="insight",
        title="Learn Test",
        body="Body",
        confidence=0.8,
        tags=[],
        source="run-2",
    )
    fm_learn = learning.to_frontmatter_dict()
    assert "kind" in fm_learn


def test_memory_type_folders():
    """MEMORY_TYPE_FOLDERS maps all MemoryType values."""
    assert MemoryType.decision in MEMORY_TYPE_FOLDERS
    assert MemoryType.learning in MEMORY_TYPE_FOLDERS
    assert MemoryType.summary in MEMORY_TYPE_FOLDERS
    assert MEMORY_TYPE_FOLDERS[MemoryType.decision] == "decisions"
    assert MEMORY_TYPE_FOLDERS[MemoryType.learning] == "learnings"
    assert MEMORY_TYPE_FOLDERS[MemoryType.summary] == "summaries"


def test_memory_write_schema_prompt():
    """memory_write_schema_prompt() returns non-empty string with field names."""
    prompt = memory_write_schema_prompt()
    assert len(prompt) > 0
    assert "id" in prompt
    assert "title" in prompt
    assert "created" in prompt
    assert "confidence" in prompt
    assert "tags" in prompt
