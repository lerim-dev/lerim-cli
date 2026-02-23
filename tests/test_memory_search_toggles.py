"""Search mode contract tests for files-only retrieval toggles."""

from __future__ import annotations

from dataclasses import replace

from lerim.app import cli
from lerim.memory.memory_record import MemoryRecord, MemoryType
from tests.helpers import make_config


def test_disabled_backends_never_execute(monkeypatch, tmp_path) -> None:
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
        primitive=MemoryType.learning,
        kind="insight",
        title="Queue lifecycle",
        body="Keep enqueue claim heartbeat complete fail lifecycle consistent.",
        confidence=0.8,
        tags=["queue"],
    )
    (learnings_dir / "20260220-queue-lifecycle.md").write_text(
        record.to_markdown(), encoding="utf-8"
    )

    monkeypatch.setattr(cli, "get_config", lambda: config)

    hits = cli.search_memory("queue lifecycle", limit=5)
    assert len(hits) == 1
    assert hits[0]["title"] == "Queue lifecycle"
