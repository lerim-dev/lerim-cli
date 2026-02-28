"""Search mode contract tests for files-only retrieval via CLI rg search."""

from __future__ import annotations

from dataclasses import replace

from lerim.memory.memory_record import MemoryRecord
from tests.helpers import make_config, run_cli


def test_memory_search_finds_seeded_file(monkeypatch, tmp_path) -> None:
    """CLI 'memory search' uses rg and finds content in seeded memory files."""
    config = replace(
        make_config(tmp_path),
        memory_scope="global_only",
        global_data_dir=tmp_path,
    )
    # Write a memory file directly
    learnings_dir = tmp_path / "memory" / "learnings"
    learnings_dir.mkdir(parents=True, exist_ok=True)
    record = MemoryRecord(
        id="queue-lifecycle",
        primitive="learning",
        kind="insight",
        title="Queue lifecycle",
        body="Keep enqueue claim heartbeat complete fail lifecycle consistent.",
        confidence=0.8,
        tags=["queue"],
    )
    (learnings_dir / "20260220-queue-lifecycle.md").write_text(
        record.to_markdown(), encoding="utf-8"
    )

    from lerim.app import cli

    monkeypatch.setattr(cli, "get_config", lambda: config)

    code, output = run_cli(["memory", "search", "queue lifecycle"])
    assert code == 0
    assert "queue" in output.lower() or "lifecycle" in output.lower()
