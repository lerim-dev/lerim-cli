"""Test indexer platform paths for session discovery across adapters."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from lerim.config.settings import reload_config
from lerim.sessions import catalog
from lerim.sessions.catalog import get_indexed_run_hashes, index_session_for_fts
from tests.helpers import write_test_config


class _FakeAdapter:
    @staticmethod
    def iter_sessions(traces_dir: Path, start=None, end=None, known_run_hashes=None):
        _ = (traces_dir, start, end, known_run_hashes)
        return [
            SimpleNamespace(
                run_id="run-x",
                agent_type="codex",
                session_path="/tmp/run-x.jsonl",
                start_time="2026-02-14T00:00:00+00:00",
                repo_path=None,
                repo_name="repo-x",
                status="completed",
                duration_ms=100,
                message_count=2,
                tool_call_count=1,
                error_count=0,
                total_tokens=42,
                summaries=["implemented fix"],
                content_hash="fakehash123",
            )
        ]

    @staticmethod
    def read_session(path: Path, session_id: str | None = None):
        _ = (path, session_id)
        return None


class _FakeCursorAdapter(_FakeAdapter):
    @staticmethod
    def iter_sessions(traces_dir: Path, start=None, end=None, known_run_hashes=None):
        _ = (traces_dir, start, end, known_run_hashes)
        return [
            SimpleNamespace(
                run_id="run-cursor-1",
                agent_type="cursor",
                session_path="/tmp/run-cursor-1.jsonl",
                start_time="2026-02-14T00:00:00+00:00",
                repo_path=None,
                repo_name="repo-x",
                status="completed",
                duration_ms=100,
                message_count=2,
                tool_call_count=1,
                error_count=0,
                total_tokens=42,
                summaries=["implemented fix"],
                content_hash="fakehash456",
            )
        ]


def test_index_new_sessions_uses_connected_paths(monkeypatch, tmp_path: Path) -> None:
    config_path = write_test_config(tmp_path)
    monkeypatch.setenv("LERIM_CONFIG", str(config_path))
    reload_config()

    monkeypatch.setattr(
        catalog.adapter_registry,
        "get_connected_platform_paths",
        lambda _p: {"codex": Path("/tmp")},
    )
    monkeypatch.setattr(
        catalog.adapter_registry, "get_connected_agents", lambda _p: ["codex"]
    )
    monkeypatch.setattr(
        catalog.adapter_registry, "get_adapter", lambda _name: _FakeAdapter
    )

    out = catalog.index_new_sessions(return_details=True)
    assert len(out) == 1
    assert out[0].run_id == "run-x"


def test_index_new_sessions_cursor_path_ingestion(monkeypatch, tmp_path: Path) -> None:
    config_path = write_test_config(tmp_path)
    monkeypatch.setenv("LERIM_CONFIG", str(config_path))
    reload_config()

    monkeypatch.setattr(
        catalog.adapter_registry,
        "get_connected_platform_paths",
        lambda _p: {"cursor": Path("/tmp")},
    )
    monkeypatch.setattr(
        catalog.adapter_registry, "get_connected_agents", lambda _p: ["cursor"]
    )
    monkeypatch.setattr(
        catalog.adapter_registry, "get_adapter", lambda _name: _FakeCursorAdapter
    )

    out = catalog.index_new_sessions(return_details=True)
    assert len(out) == 1
    assert out[0].run_id == "run-cursor-1"


def test_index_new_sessions_marks_changed_when_hash_differs(
    monkeypatch, tmp_path: Path
) -> None:
    """index_new_sessions sets changed=True when a known run_id has a new hash."""
    config_path = write_test_config(tmp_path)
    monkeypatch.setenv("LERIM_CONFIG", str(config_path))
    reload_config()

    # Pre-seed an existing session with a different hash
    index_session_for_fts(
        run_id="run-x",
        agent_type="codex",
        content="old content",
        content_hash="oldhash",
    )

    monkeypatch.setattr(
        catalog.adapter_registry,
        "get_connected_platform_paths",
        lambda _p: {"codex": Path("/tmp")},
    )
    monkeypatch.setattr(
        catalog.adapter_registry, "get_connected_agents", lambda _p: ["codex"]
    )
    monkeypatch.setattr(
        catalog.adapter_registry, "get_adapter", lambda _name: _FakeAdapter
    )

    out = catalog.index_new_sessions(return_details=True)
    # The adapter returns run-x with content_hash="fakehash123" which
    # differs from "oldhash" → session is returned with changed=True
    assert len(out) == 1
    assert out[0].run_id == "run-x"
    assert out[0].changed is True

    # Verify the hash was updated in the DB
    hashes = get_indexed_run_hashes()
    assert hashes["run-x"] == "fakehash123"


def test_index_new_sessions_marks_new_as_not_changed(
    monkeypatch, tmp_path: Path
) -> None:
    """index_new_sessions sets changed=False for brand-new sessions."""
    config_path = write_test_config(tmp_path)
    monkeypatch.setenv("LERIM_CONFIG", str(config_path))
    reload_config()

    monkeypatch.setattr(
        catalog.adapter_registry,
        "get_connected_platform_paths",
        lambda _p: {"codex": Path("/tmp")},
    )
    monkeypatch.setattr(
        catalog.adapter_registry, "get_connected_agents", lambda _p: ["codex"]
    )
    monkeypatch.setattr(
        catalog.adapter_registry, "get_adapter", lambda _name: _FakeAdapter
    )

    out = catalog.index_new_sessions(return_details=True)
    assert len(out) == 1
    assert out[0].run_id == "run-x"
    assert out[0].changed is False
