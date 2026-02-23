"""Memory access tracker — records when memories are fully read or updated.

Stores access stats in <data_dir>/index/memories.sqlite3. Two signals:
- Chat flow: PostToolUse on Read (full-body reads) → memory was useful to a user
- Sync/Maintain flow: PostToolUse on Write|Edit → memory received fresh content

Used by the maintain flow to compute decay-based archiving decisions.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

# Reads with limit <= this are treated as frontmatter-only scans (not tracked).
FRONTMATTER_LINE_LIMIT = 20

# Memory primitive folders where tracking applies.
_MEMORY_FOLDERS = {"decisions", "learnings"}

# Regex: extract YYYYMMDD-slug from filename like "20260221-some-slug.md"
_MEMORY_ID_RE = re.compile(r"^(\d{8}-.+)\.md$")


def _connect(db_path: Path) -> sqlite3.Connection:
    """Open memories.sqlite3 with WAL mode and dict row factory."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.row_factory = sqlite3.Row
    return conn


def init_access_db(db_path: Path) -> None:
    """Create memory_access table if missing."""
    with _connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS memory_access (
                memory_id     TEXT NOT NULL,
                memory_root   TEXT NOT NULL,
                last_accessed TEXT NOT NULL,
                access_count  INTEGER DEFAULT 1,
                PRIMARY KEY (memory_id, memory_root)
            )
        """)


def record_access(db_path: Path, memory_id: str, memory_root: str) -> None:
    """Upsert access timestamp and bump count for a memory."""
    now = datetime.now(timezone.utc).isoformat()
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO memory_access (memory_id, memory_root, last_accessed, access_count)
            VALUES (?, ?, ?, 1)
            ON CONFLICT(memory_id, memory_root) DO UPDATE SET
                last_accessed = excluded.last_accessed,
                access_count  = access_count + 1
            """,
            (memory_id, memory_root, now),
        )


def get_access_stats(db_path: Path, memory_root: str) -> list[dict[str, Any]]:
    """Return all access records for a memory root as list of dicts."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            """SELECT memory_id, last_accessed, access_count \
FROM memory_access WHERE memory_root = ? ORDER BY last_accessed DESC""",
            (memory_root,),
        ).fetchall()
    return [dict(row) for row in rows]


def is_body_read(tool_input: dict[str, Any]) -> bool:
    """Return True if Read tool call accessed full body (not just frontmatter scan)."""
    limit = tool_input.get("limit")
    return limit is None or limit > FRONTMATTER_LINE_LIMIT


def extract_memory_id(file_path: str, memory_root: str) -> str | None:
    """Extract memory_id from file_path if it's inside a memory primitive folder.

    Returns the filename stem (e.g. '20260221-some-slug') or None if the path
    is not a recognized memory file.
    """
    try:
        resolved = Path(file_path).resolve()
        root = Path(memory_root).resolve()
    except (ValueError, OSError):
        return None
    # Check the file sits directly inside memory_root/{decisions,learnings}/
    if resolved.parent.parent != root:
        return None
    if resolved.parent.name not in _MEMORY_FOLDERS:
        return None
    match = _MEMORY_ID_RE.match(resolved.name)
    return match.group(1) if match else None


if __name__ == "__main__":
    """Real-path smoke test for access tracker."""
    with TemporaryDirectory() as tmp:
        db = Path(tmp) / "index" / "memories.sqlite3"
        mem_root = Path(tmp) / "memory"

        # init creates table
        init_access_db(db)
        assert db.exists()

        # record + query
        record_access(db, "20260221-deploy-tips", str(mem_root))
        record_access(db, "20260221-deploy-tips", str(mem_root))
        stats = get_access_stats(db, str(mem_root))
        assert len(stats) == 1
        assert stats[0]["memory_id"] == "20260221-deploy-tips"
        assert stats[0]["access_count"] == 2

        # different memory root
        other_root = Path(tmp) / "other"
        record_access(db, "20260221-deploy-tips", str(other_root))
        assert len(get_access_stats(db, str(other_root))) == 1
        assert len(get_access_stats(db, str(mem_root))) == 1

        # is_body_read
        assert is_body_read({}) is True
        assert is_body_read({"limit": 2000}) is True
        assert is_body_read({"limit": 15}) is False
        assert is_body_read({"limit": 20}) is False
        assert is_body_read({"limit": 21}) is True

        # extract_memory_id
        decisions = mem_root / "decisions"
        decisions.mkdir(parents=True)
        mid = extract_memory_id(
            str(decisions / "20260221-deploy-tips.md"), str(mem_root)
        )
        assert mid == "20260221-deploy-tips"

        # non-memory path returns None
        assert extract_memory_id("/tmp/random.md", str(mem_root)) is None
        # wrong folder returns None
        summaries = mem_root / "summaries"
        summaries.mkdir(parents=True)
        assert (
            extract_memory_id(str(summaries / "20260221-deploy-tips.md"), str(mem_root))
            is None
        )
        # non-.md returns None
        assert extract_memory_id(str(decisions / "readme.txt"), str(mem_root)) is None

    print("access_tracker: all self-tests passed")
