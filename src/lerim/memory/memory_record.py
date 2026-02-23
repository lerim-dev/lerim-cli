"""Canonical memory taxonomy, on-disk record model, and markdown helpers.

MemoryRecord subclasses MemoryCandidate (DSPy extraction schema) and adds
bookkeeping fields for persisted memory files.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from enum import Enum

import frontmatter
from pydantic import Field

from lerim.memory.schemas import MemoryCandidate


class MemoryType(str, Enum):
    """Canonical memory types used across runtime, pipelines, and storage."""

    decision = "decision"
    learning = "learning"
    summary = "summary"


MEMORY_TYPE_FOLDERS: dict[MemoryType, str] = {
    MemoryType.decision: "decisions",
    MemoryType.learning: "learnings",
    MemoryType.summary: "summaries",
}

# Canonical required frontmatter fields per primitive type.
# Used by the memory-write prompt (tells the agent what to write) and
# the PreToolUse hook (normalizes/enforces before Write hits disk).
MEMORY_FRONTMATTER_SCHEMA: dict[MemoryType, list[str]] = {
    MemoryType.decision: [
        "id",
        "title",
        "created",
        "updated",
        "source",
        "confidence",
        "tags",
    ],
    MemoryType.learning: [
        "id",
        "title",
        "created",
        "updated",
        "source",
        "kind",
        "confidence",
        "tags",
    ],
}


def slugify(value: str) -> str:
    """Generate a filesystem-safe ASCII slug from text."""
    raw = (
        unicodedata.normalize("NFKD", str(value or ""))
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", raw.strip().lower()).strip("-")
    return cleaned or "memory"


def canonical_memory_filename(*, title: str, run_id: str) -> str:
    """Build canonical filename: ``{YYYYMMDD}-{slug}.md``.

    Uses the date portion of run_id (format ``sync-YYYYMMDD-HHMMSS-hex``) when
    available, otherwise today's date.
    """
    slug = slugify(title)
    parts = (run_id or "").split("-")
    date_str = next((p for p in parts if len(p) == 8 and p.isdigit()), None)
    if not date_str:
        date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"{date_str}-{slug}.md"


def memory_folder(memory_type: MemoryType) -> str:
    """Return canonical folder name for one memory type."""
    return MEMORY_TYPE_FOLDERS[memory_type]


def memory_write_schema_prompt() -> str:
    """Return a prompt-ready description of memory file naming and frontmatter rules."""
    lines = [
        "Memory file write rules (strict):",
        "- Filename format: {YYYYMMDD}-{slug}.md where slug is the slugified title.",
        "- Every memory file must start with YAML frontmatter between --- delimiters.",
        "- Required frontmatter fields per type:",
    ]
    for ptype, fields in MEMORY_FRONTMATTER_SCHEMA.items():
        lines.append(f"  {ptype.value}: {', '.join(fields)}")
    lines += [
        "- Field rules:",
        "  - id: slugified title (lowercase, hyphens, no special chars).",
        "  - created/updated: ISO 8601 UTC (e.g. 2026-02-20T23:10:32Z).",
        "  - source: the run_id from metadata.",
        "  - confidence: float 0.0-1.0.",
        "  - kind: one of insight, procedure, friction, pitfall, preference.",
        "  - tags: list of group/cluster labels.",
        "  - Do not add extra fields beyond the required set.",
        "- Body follows the closing --- delimiter as plain text/markdown.",
        "- Summaries are written directly by the summarization pipeline, not by the agent.",
    ]
    return "\n".join(lines)


class MemoryRecord(MemoryCandidate):
    """On-disk memory record for decisions/learnings.

    Subclasses MemoryCandidate (DSPy extraction schema) and adds bookkeeping
    fields: id, created, updated, source.
    """

    id: str
    created: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source: str = ""

    def to_frontmatter_dict(self) -> dict:
        """Build minimal frontmatter payload based on primitive type."""
        base: dict = {
            "id": self.id,
            "title": self.title,
            "created": self.created.isoformat(),
            "updated": self.updated.isoformat(),
            "source": self.source,
            "confidence": self.confidence,
            "tags": list(self.tags),
        }
        if self.primitive == MemoryType.learning:
            base["kind"] = self.kind or "insight"
        return base

    def to_markdown(self) -> str:
        """Serialize record to frontmatter + body markdown format."""
        post = frontmatter.Post(self.body, **self.to_frontmatter_dict())
        return frontmatter.dumps(post) + "\n"


if __name__ == "__main__":
    """Run a real-path self-test for MemoryRecord serialization."""
    record = MemoryRecord(
        id="queue-lifecycle",
        primitive="learning",
        kind="insight",
        title="Queue lifecycle",
        body="Keep queue states explicit.",
        confidence=0.8,
        tags=["queue", "reliability"],
        source="self-test-run",
    )
    md = record.to_markdown()
    assert "---" in md
    assert "queue-lifecycle" in md
    assert "Queue lifecycle" in md
    assert "Keep queue states explicit." in md

    fm_dict = record.to_frontmatter_dict()
    assert fm_dict["id"] == "queue-lifecycle"
    assert fm_dict["kind"] == "insight"
    assert fm_dict["tags"] == ["queue", "reliability"]
    assert "confidence" in fm_dict

    # Verify slugify
    assert slugify("Hello World!") == "hello-world"
    assert slugify("") == "memory"
    assert slugify("  --test--  ") == "test"

    # Verify canonical_memory_filename
    fname = canonical_memory_filename(
        title="My Title",
        run_id="sync-20260220-120000-abc123",
    )
    assert fname == "20260220-my-title.md"
