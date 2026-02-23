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
from lerim.runtime.prompts.chat import build_chat_prompt
from lerim.runtime.prompts.maintain import (
    build_maintain_artifact_paths,
    build_maintain_prompt,
)
from lerim.runtime.tools import build_tool_context, read_file_tool, write_file_tool
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


class TestRuntimeToolAccessTracking:
    """Runtime read/write tools emit access tracker updates."""

    def _context(self, tmp_path: Path):
        cfg = make_config(tmp_path)
        init_access_db(cfg.memories_db_path)
        memory_root = tmp_path / "memory"
        workspace_root = tmp_path / "workspace"
        run_folder = workspace_root / "sync-20260223-000000-aaaaaa"
        (memory_root / "decisions").mkdir(parents=True)
        (memory_root / "learnings").mkdir(parents=True)
        run_folder.mkdir(parents=True)
        return build_tool_context(
            repo_root=tmp_path,
            memory_root=memory_root,
            workspace_root=workspace_root,
            run_folder=run_folder,
            run_id=run_folder.name,
            config=cfg,
        )

    def test_read_tool_skips_frontmatter_scan(self, tmp_path: Path) -> None:
        context = self._context(tmp_path)
        assert context.memory_root is not None
        target = context.memory_root / "decisions" / "20260221-tips.md"
        target.write_text("---\ntitle: Tips\n---\nBody\n", encoding="utf-8")

        read_file_tool(
            context=context,
            file_path=str(target),
            offset=1,
            limit=15,
        )
        stats = get_access_stats(
            context.config.memories_db_path, str(context.memory_root)
        )
        assert stats == []

    def test_read_tool_tracks_full_body(self, tmp_path: Path) -> None:
        context = self._context(tmp_path)
        assert context.memory_root is not None
        target = context.memory_root / "decisions" / "20260221-tips.md"
        target.write_text("---\ntitle: Tips\n---\nBody\n", encoding="utf-8")

        read_file_tool(
            context=context,
            file_path=str(target),
            offset=1,
            limit=200,
        )
        stats = get_access_stats(
            context.config.memories_db_path, str(context.memory_root)
        )
        assert len(stats) == 1
        assert stats[0]["memory_id"] == "20260221-tips"

    def test_write_tool_tracks_memory_write(self, tmp_path: Path) -> None:
        context = self._context(tmp_path)
        assert context.memory_root is not None
        write_file_tool(
            context=context,
            file_path=str(context.memory_root / "learnings" / "draft.md"),
            content=(
                "---\n"
                "title: Queue pattern\n"
                "confidence: 0.8\n"
                "tags: [queue]\n"
                "---\n"
                "Keep claim and heartbeat flow deterministic.\n"
            ),
        )
        stats = get_access_stats(
            context.config.memories_db_path, str(context.memory_root)
        )
        assert len(stats) == 1
        assert stats[0]["memory_id"].endswith("-queue-pattern")


class TestChatPromptMemoryRoot:
    """Chat prompt includes memory guidance when memory_root is provided."""

    def test_guidance_with_memory_root(self) -> None:
        prompt = build_chat_prompt("test", [], [], memory_root="/data/memory")
        assert "Memory location: /data/memory" in prompt
        assert "two-phase retrieval" in prompt

    def test_no_guidance_without_memory_root(self) -> None:
        prompt = build_chat_prompt("test", [], [])
        assert "Memory location" not in prompt


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
