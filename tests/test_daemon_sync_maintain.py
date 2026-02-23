"""Test daemon sync and maintain paths."""

from __future__ import annotations

from lerim.app import daemon
from lerim.config.settings import reload_config
from lerim.sessions import catalog
from tests.helpers import write_test_config


def _setup(tmp_path, monkeypatch) -> None:
    """Set up test environment with tmp dirs and config."""
    config_path = write_test_config(tmp_path)
    monkeypatch.setenv("LERIM_CONFIG", str(config_path))
    reload_config()
    catalog.init_sessions_db()


def test_sync_does_not_run_vector_rebuild(monkeypatch, tmp_path) -> None:
    """Sync flow does not trigger vector rebuild side-effects."""
    _setup(tmp_path, monkeypatch)
    session_path = tmp_path / "sessions" / "run-sync-1.jsonl"
    session_path.parent.mkdir(parents=True, exist_ok=True)
    session_path.write_text('{"role":"assistant","content":"ok"}\n', encoding="utf-8")
    catalog.index_session_for_fts(
        run_id="run-sync-1",
        agent_type="codex",
        content="session content",
        session_path=str(session_path),
    )

    monkeypatch.setattr(
        "lerim.runtime.agent.LerimAgent.sync",
        lambda *_args, **_kwargs: {
            "counts": {"add": 1, "update": 0, "no_op": 0},
        },
    )

    code, summary = daemon.run_sync_once(
        run_id="run-sync-1",
        agent_filter=None,
        no_extract=False,
        force=False,
        max_sessions=1,
        dry_run=False,
        ignore_lock=True,
        trigger="test",
        window_start=None,
        window_end=None,
    )

    latest = catalog.latest_service_run("sync")
    assert code == daemon.EXIT_OK
    assert summary.extracted_sessions == 1
    assert latest is not None
    assert "vectors_updated" not in latest["details"]
    assert "vectors_error" not in latest["details"]


def test_maintain_calls_agent(monkeypatch, tmp_path) -> None:
    """Maintain flow calls LerimAgent.maintain() and returns result."""
    _setup(tmp_path, monkeypatch)
    called = []
    monkeypatch.setattr(
        "lerim.runtime.agent.LerimAgent.maintain",
        lambda self, **kw: (
            called.append(True),
            {
                "counts": {
                    "merged": 0,
                    "archived": 0,
                    "consolidated": 0,
                    "unchanged": 0,
                }
            },
        )[1],
    )
    code, payload = daemon.run_maintain_once(force=False, dry_run=False)
    assert code == daemon.EXIT_OK
    assert len(called) == 1
