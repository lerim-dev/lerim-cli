"""Unit tests for memory access tracker.

Tests: init_access_db, record_access, get_access_stats, is_body_read,
extract_memory_id.
"""

from __future__ import annotations

from lerim.memory.access_tracker import (
    FRONTMATTER_LINE_LIMIT,
    extract_memory_id,
    get_access_stats,
    init_access_db,
    is_body_read,
    record_access,
)


# ---------------------------------------------------------------------------
# init_access_db
# ---------------------------------------------------------------------------


def test_init_creates_db_file(tmp_path):
    """init_access_db creates the SQLite database file."""
    db = tmp_path / "index" / "memories.sqlite3"
    init_access_db(db)
    assert db.exists()


def test_init_creates_parent_dirs(tmp_path):
    """init_access_db creates parent directories if missing."""
    db = tmp_path / "deep" / "nested" / "memories.sqlite3"
    init_access_db(db)
    assert db.exists()


def test_init_idempotent(tmp_path):
    """Calling init_access_db twice does not raise."""
    db = tmp_path / "memories.sqlite3"
    init_access_db(db)
    init_access_db(db)
    assert db.exists()


# ---------------------------------------------------------------------------
# record_access + get_access_stats
# ---------------------------------------------------------------------------


def test_record_and_query(tmp_path):
    """record_access stores entry, get_access_stats retrieves it."""
    db = tmp_path / "memories.sqlite3"
    init_access_db(db)
    mem_root = str(tmp_path / "memory")

    record_access(db, "20260228-test-slug", mem_root)
    stats = get_access_stats(db, mem_root)

    assert len(stats) == 1
    assert stats[0]["memory_id"] == "20260228-test-slug"
    assert stats[0]["access_count"] == 1


def test_record_bumps_count(tmp_path):
    """Repeated record_access increments access_count via upsert."""
    db = tmp_path / "memories.sqlite3"
    init_access_db(db)
    mem_root = str(tmp_path / "memory")

    record_access(db, "20260228-test", mem_root)
    record_access(db, "20260228-test", mem_root)
    record_access(db, "20260228-test", mem_root)

    stats = get_access_stats(db, mem_root)
    assert stats[0]["access_count"] == 3


def test_record_updates_timestamp(tmp_path):
    """record_access updates last_accessed on each call."""
    db = tmp_path / "memories.sqlite3"
    init_access_db(db)
    mem_root = str(tmp_path / "memory")

    record_access(db, "20260228-test", mem_root)
    stats1 = get_access_stats(db, mem_root)
    ts1 = stats1[0]["last_accessed"]

    record_access(db, "20260228-test", mem_root)
    stats2 = get_access_stats(db, mem_root)
    ts2 = stats2[0]["last_accessed"]

    assert ts2 >= ts1


def test_different_roots_isolated(tmp_path):
    """Records in different memory roots are isolated."""
    db = tmp_path / "memories.sqlite3"
    init_access_db(db)
    root_a = str(tmp_path / "project-a" / "memory")
    root_b = str(tmp_path / "project-b" / "memory")

    record_access(db, "20260228-shared-slug", root_a)
    record_access(db, "20260228-shared-slug", root_b)

    assert len(get_access_stats(db, root_a)) == 1
    assert len(get_access_stats(db, root_b)) == 1


def test_multiple_memories_same_root(tmp_path):
    """Multiple different memories in same root tracked independently."""
    db = tmp_path / "memories.sqlite3"
    init_access_db(db)
    mem_root = str(tmp_path / "memory")

    record_access(db, "20260228-decision-a", mem_root)
    record_access(db, "20260228-learning-b", mem_root)
    record_access(db, "20260228-decision-a", mem_root)

    stats = get_access_stats(db, mem_root)
    by_id = {s["memory_id"]: s for s in stats}
    assert by_id["20260228-decision-a"]["access_count"] == 2
    assert by_id["20260228-learning-b"]["access_count"] == 1


def test_stats_ordered_by_last_accessed_desc(tmp_path):
    """get_access_stats returns records ordered by last_accessed descending."""
    db = tmp_path / "memories.sqlite3"
    init_access_db(db)
    mem_root = str(tmp_path / "memory")

    record_access(db, "20260228-old", mem_root)
    record_access(db, "20260228-new", mem_root)

    stats = get_access_stats(db, mem_root)
    assert stats[0]["memory_id"] == "20260228-new"


def test_empty_root_returns_empty(tmp_path):
    """get_access_stats for unrecorded root returns empty list."""
    db = tmp_path / "memories.sqlite3"
    init_access_db(db)
    assert get_access_stats(db, "/nonexistent") == []


# ---------------------------------------------------------------------------
# is_body_read
# ---------------------------------------------------------------------------


def test_is_body_read_no_limit():
    """No limit means full file read — returns True."""
    assert is_body_read({}) is True


def test_is_body_read_large_limit():
    """Limit larger than frontmatter threshold means body read."""
    assert is_body_read({"limit": FRONTMATTER_LINE_LIMIT + 1}) is True
    assert is_body_read({"limit": 2000}) is True


def test_is_body_read_frontmatter_only():
    """Limit at or below threshold means frontmatter-only scan."""
    assert is_body_read({"limit": FRONTMATTER_LINE_LIMIT}) is False
    assert is_body_read({"limit": 5}) is False
    assert is_body_read({"limit": 1}) is False


def test_is_body_read_boundary():
    """Boundary: limit == FRONTMATTER_LINE_LIMIT is not body read, +1 is."""
    assert is_body_read({"limit": FRONTMATTER_LINE_LIMIT}) is False
    assert is_body_read({"limit": FRONTMATTER_LINE_LIMIT + 1}) is True


# ---------------------------------------------------------------------------
# extract_memory_id
# ---------------------------------------------------------------------------


def test_extract_memory_id_decisions(tmp_path):
    """extract_memory_id returns slug for file in decisions/ folder."""
    mem_root = tmp_path / "memory"
    decisions = mem_root / "decisions"
    decisions.mkdir(parents=True)

    file_path = str(decisions / "20260228-auth-pattern.md")
    result = extract_memory_id(file_path, str(mem_root))
    assert result == "20260228-auth-pattern"


def test_extract_memory_id_learnings(tmp_path):
    """extract_memory_id returns slug for file in learnings/ folder."""
    mem_root = tmp_path / "memory"
    learnings = mem_root / "learnings"
    learnings.mkdir(parents=True)

    file_path = str(learnings / "20260228-queue-fix.md")
    result = extract_memory_id(file_path, str(mem_root))
    assert result == "20260228-queue-fix"


def test_extract_memory_id_wrong_folder(tmp_path):
    """extract_memory_id returns None for file in non-primitive folder."""
    mem_root = tmp_path / "memory"
    summaries = mem_root / "summaries"
    summaries.mkdir(parents=True)

    assert extract_memory_id(
        str(summaries / "20260228-summary.md"), str(mem_root)
    ) is None


def test_extract_memory_id_archived_returns_none(tmp_path):
    """extract_memory_id returns None for archived files (nested too deep)."""
    mem_root = tmp_path / "memory"
    archived = mem_root / "archived" / "decisions"
    archived.mkdir(parents=True)

    assert extract_memory_id(
        str(archived / "20260228-old.md"), str(mem_root)
    ) is None


def test_extract_memory_id_non_md(tmp_path):
    """extract_memory_id returns None for non-.md files."""
    mem_root = tmp_path / "memory"
    decisions = mem_root / "decisions"
    decisions.mkdir(parents=True)

    assert extract_memory_id(
        str(decisions / "readme.txt"), str(mem_root)
    ) is None


def test_extract_memory_id_bad_date_prefix(tmp_path):
    """extract_memory_id returns None when filename lacks YYYYMMDD- prefix."""
    mem_root = tmp_path / "memory"
    decisions = mem_root / "decisions"
    decisions.mkdir(parents=True)

    assert extract_memory_id(
        str(decisions / "no-date-prefix.md"), str(mem_root)
    ) is None


def test_extract_memory_id_outside_root():
    """extract_memory_id returns None for files outside memory root."""
    assert extract_memory_id("/tmp/random/file.md", "/home/user/memory") is None


def test_extract_memory_id_invalid_path():
    """extract_memory_id handles invalid path gracefully."""
    assert extract_memory_id("", "/memory") is None
