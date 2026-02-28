"""Unit tests for write_summary_markdown in the summarization pipeline."""

from __future__ import annotations

import frontmatter

from lerim.memory.summarization_pipeline import write_summary_markdown


def _sample_payload(title: str = "Auth setup session") -> dict:
    """Build a minimal summary payload dict for testing."""
    return {
        "title": title,
        "description": "Set up JWT auth with HS256.",
        "user_intent": "Configure authentication for the API.",
        "session_narrative": "Decided on JWT with HS256, configured middleware.",
        "date": "2026-02-20",
        "time": "10:01:05",
        "coding_agent": "claude",
        "raw_trace_path": "/tmp/trace.jsonl",
        "run_id": "sync-20260220-100100-abc",
        "repo_name": "my-project",
        "tags": ["auth", "jwt"],
    }


def test_write_summary_creates_correct_path(tmp_path):
    """Summary written to memory_root/summaries/YYYYMMDD/HHMMSS/{slug}.md."""
    memory_root = tmp_path / "memory"
    memory_root.mkdir()
    result = write_summary_markdown(_sample_payload(), memory_root, run_id="run-1")
    assert result.exists()
    assert "summaries" in str(result)
    assert result.suffix == ".md"
    # Path structure: memory_root/summaries/YYYYMMDD/HHMMSS/slug.md
    parts = result.relative_to(memory_root).parts
    assert parts[0] == "summaries"
    assert len(parts) == 4  # summaries / YYYYMMDD / HHMMSS / slug.md


def test_write_summary_frontmatter_fields(tmp_path):
    """Written file has all required frontmatter fields."""
    memory_root = tmp_path / "memory"
    memory_root.mkdir()
    result = write_summary_markdown(_sample_payload(), memory_root, run_id="run-1")
    parsed = frontmatter.load(str(result))
    required = {
        "id",
        "title",
        "created",
        "source",
        "description",
        "date",
        "time",
        "coding_agent",
        "raw_trace_path",
        "run_id",
        "repo_name",
        "tags",
    }
    for key in required:
        assert key in parsed.metadata, f"Missing frontmatter field: {key}"


def test_write_summary_body_has_sections(tmp_path):
    """Body contains '## User Intent' and '## What Happened' sections."""
    memory_root = tmp_path / "memory"
    memory_root.mkdir()
    result = write_summary_markdown(_sample_payload(), memory_root, run_id="run-1")
    content = result.read_text(encoding="utf-8")
    assert "## User Intent" in content
    assert "## What Happened" in content


def test_write_summary_slug_matches_title(tmp_path):
    """Filename slug is derived from title."""
    memory_root = tmp_path / "memory"
    memory_root.mkdir()
    result = write_summary_markdown(
        _sample_payload("My Special Title"), memory_root, run_id="run-1"
    )
    assert "my-special-title" in result.name
