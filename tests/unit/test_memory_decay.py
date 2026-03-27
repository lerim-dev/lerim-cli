"""Tests for memory access decay signals and decay-related prompts/config."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from lerim.memory.access_tracker import (
    FRONTMATTER_LINE_LIMIT,
    extract_memory_id,
    get_access_stats,
    init_access_db,
    is_body_read,
    record_access,
)
from lerim.memory.memory_repo import build_memory_paths
from lerim.runtime.prompts.ask import build_ask_prompt
from lerim.runtime.prompts.maintain import (
    build_maintain_artifact_paths,
    build_maintain_prompt,
)
from tests.helpers import make_config


class TestAccessTrackerDB:
    """SQLite access tracking CRUD."""

    def test_init_creates_table(self, tmp_path: Path) -> None:
        db = tmp_path / "index" / "memories.sqlite3"
        init_access_db(db)
        assert db.exists()

    def test_record_and_query(self, tmp_path: Path) -> None:
        db = tmp_path / "memories.sqlite3"
        init_access_db(db)
        record_access(db, "20260221-deploy-tips", "/mem")
        stats = get_access_stats(db, "/mem")
        assert len(stats) == 1
        assert stats[0]["memory_id"] == "20260221-deploy-tips"
        assert stats[0]["access_count"] == 1

    def test_upsert_increments_count(self, tmp_path: Path) -> None:
        db = tmp_path / "memories.sqlite3"
        init_access_db(db)
        record_access(db, "20260221-tips", "/mem")
        record_access(db, "20260221-tips", "/mem")
        record_access(db, "20260221-tips", "/mem")
        stats = get_access_stats(db, "/mem")
        assert stats[0]["access_count"] == 3


class TestIsBodyRead:
    """Distinguish frontmatter scans from full-body reads."""

    def test_threshold_behavior(self) -> None:
        assert is_body_read({}) is True
        assert is_body_read({"limit": 2000}) is True
        assert is_body_read({"limit": FRONTMATTER_LINE_LIMIT + 1}) is True
        assert is_body_read({"limit": FRONTMATTER_LINE_LIMIT}) is False
        assert is_body_read({"limit": 15}) is False


class TestExtractMemoryId:
    """Memory ID extraction from file paths."""

    def test_valid_paths(self, tmp_path: Path) -> None:
        mem_root = tmp_path / "memory"
        (mem_root / "decisions").mkdir(parents=True)
        (mem_root / "learnings").mkdir(parents=True)
        assert (
            extract_memory_id(
                str(mem_root / "decisions" / "20260221-deploy-tips.md"),
                str(mem_root),
            )
            == "20260221-deploy-tips"
        )
        assert (
            extract_memory_id(
                str(mem_root / "learnings" / "20260101-use-uv.md"),
                str(mem_root),
            )
            == "20260101-use-uv"
        )

    def test_invalid_paths(self, tmp_path: Path) -> None:
        mem_root = tmp_path / "memory"
        (mem_root / "summaries").mkdir(parents=True)
        assert extract_memory_id("/tmp/random/file.md", str(mem_root)) is None
        assert (
            extract_memory_id(
                str(mem_root / "summaries" / "20260101-summary.md"), str(mem_root)
            )
            is None
        )


class TestAskPromptMemoryRoot:
    """Ask prompt includes memory guidance when memory_root is provided."""

    def test_guidance_with_memory_root(self) -> None:
        prompt = build_ask_prompt("test", [], [], memory_root="/data/memory")
        assert "Memory root: /data/memory" in prompt
        assert "grep" in prompt
        assert "decisions/*.md" in prompt

    def test_no_guidance_without_memory_root(self) -> None:
        prompt = build_ask_prompt("test", [], [])
        assert "Memory root" not in prompt


class TestMaintainPromptDecay:
    """Maintain prompt includes decay check section and fields."""

    def test_decay_section_and_report_keys(self, tmp_path: Path) -> None:
        artifact_paths = build_maintain_artifact_paths(tmp_path)
        prompt = build_maintain_prompt(
            memory_root=tmp_path,
            run_folder=tmp_path,
            artifact_paths=artifact_paths,
        )
        assert "decay_check" in prompt
        assert '"decayed"' in prompt

    def test_stats_rendering(self, tmp_path: Path) -> None:
        artifact_paths = build_maintain_artifact_paths(tmp_path)
        prompt = build_maintain_prompt(
            memory_root=tmp_path,
            run_folder=tmp_path,
            artifact_paths=artifact_paths,
            access_stats=[
                {
                    "memory_id": "20260221-tips",
                    "last_accessed": "2026-02-21T10:00:00Z",
                    "access_count": 5,
                }
            ],
            decay_days=365,
            decay_archive_threshold=0.3,
            decay_min_confidence_floor=0.05,
            decay_recent_access_grace_days=60,
        )
        assert "20260221-tips" in prompt
        assert "365" in prompt
        assert "0.3" in prompt
        assert "0.05" in prompt
        assert "60" in prompt


class TestMemoryPathsAndConfig:
    """Memory path and config fields include decay and memory DB values."""

    def test_memory_paths(self, tmp_path: Path) -> None:
        paths = build_memory_paths(tmp_path)
        assert paths.memories_db_path == tmp_path / "index" / "memories.sqlite3"
        assert paths.graph_db_path == tmp_path / "index" / "graph.sqlite3"

    def test_config_decay_fields(self, tmp_path: Path) -> None:
        cfg = make_config(tmp_path)
        assert cfg.decay_enabled is True
        assert cfg.decay_days == 180
        assert cfg.decay_min_confidence_floor == 0.1
        assert cfg.decay_archive_threshold == 0.2
        assert cfg.decay_recent_access_grace_days == 30
        overridden = replace(cfg, decay_days=365, decay_archive_threshold=0.5)
        assert overridden.decay_days == 365
        assert overridden.decay_archive_threshold == 0.5
