"""Test daemon sync and maintain paths."""

from __future__ import annotations

import time

from lerim.app import daemon
from lerim.config.settings import reload_config
from lerim.sessions import catalog
from tests.helpers import make_config, write_test_config


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


def test_sync_force_enqueues_changed_sessions(monkeypatch, tmp_path) -> None:
    """Changed sessions (hash differs) are force-enqueued so they get re-extracted."""
    _setup(tmp_path, monkeypatch)

    # Pre-seed a session that was already indexed and completed
    session_path = tmp_path / "sessions" / "run-changed-1.jsonl"
    session_path.parent.mkdir(parents=True, exist_ok=True)
    session_path.write_text('{"role":"user","content":"original"}\n', encoding="utf-8")
    catalog.index_session_for_fts(
        run_id="run-changed-1",
        agent_type="codex",
        content="old content",
        session_path=str(session_path),
        content_hash="oldhash",
    )
    catalog.enqueue_session_job("run-changed-1", session_path=str(session_path))
    jobs = catalog.claim_session_jobs(limit=1, run_ids=["run-changed-1"])
    assert len(jobs) == 1
    catalog.complete_session_job("run-changed-1")

    # Simulate index_new_sessions returning this session as changed
    from lerim.sessions.catalog import IndexedSession

    monkeypatch.setattr(
        "lerim.app.daemon.index_new_sessions",
        lambda **kw: [
            IndexedSession(
                run_id="run-changed-1",
                agent_type="codex",
                session_path=str(session_path),
                start_time="2026-02-20T10:00:00Z",
                changed=True,
            )
        ],
    )
    monkeypatch.setattr(
        "lerim.runtime.agent.LerimAgent.sync",
        lambda *_a, **_kw: {"counts": {"add": 0, "update": 1, "no_op": 0}},
    )

    code, summary = daemon.run_sync_once(
        run_id=None,
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
    assert code == daemon.EXIT_OK
    # The changed session was force-enqueued and extracted
    assert summary.extracted_sessions == 1


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


def test_config_has_separate_interval_fields(tmp_path) -> None:
    """Config dataclass exposes sync_interval_minutes and maintain_interval_minutes."""
    cfg = make_config(tmp_path)
    assert hasattr(cfg, "sync_interval_minutes")
    assert hasattr(cfg, "maintain_interval_minutes")
    assert isinstance(cfg.sync_interval_minutes, int)
    assert isinstance(cfg.maintain_interval_minutes, int)
    public = cfg.public_dict()
    assert "sync_interval_minutes" in public
    assert "maintain_interval_minutes" in public


def test_daemon_sync_runs_more_often_than_maintain(monkeypatch, tmp_path) -> None:
    """With sync interval < maintain interval, sync runs more often."""
    import dataclasses

    _setup(tmp_path, monkeypatch)

    sync_count = 0
    maintain_count = 0
    iteration = 0

    def fake_sync_cycle():
        nonlocal sync_count
        sync_count += 1
        return daemon.EXIT_OK, daemon._empty_sync_summary()

    def fake_maintain_cycle():
        nonlocal maintain_count
        maintain_count += 1
        return daemon.EXIT_OK, {}

    monkeypatch.setattr(daemon, "_run_sync_cycle", fake_sync_cycle)
    monkeypatch.setattr(daemon, "_run_maintain_cycle", fake_maintain_cycle)

    # Patch time.sleep to advance a virtual clock instead of actually sleeping.
    virtual_clock = [0.0]
    real_monotonic = time.monotonic

    def fake_monotonic():
        return real_monotonic() + virtual_clock[0]

    def fake_sleep(seconds):
        nonlocal iteration
        iteration += 1
        virtual_clock[0] += seconds
        if iteration >= 20:
            raise KeyboardInterrupt

    monkeypatch.setattr(time, "monotonic", fake_monotonic)
    monkeypatch.setattr(time, "sleep", fake_sleep)

    # Config with sync every 1 min, maintain every 5 min
    cfg = dataclasses.replace(
        make_config(tmp_path),
        sync_interval_minutes=1,
        maintain_interval_minutes=5,
    )
    monkeypatch.setattr(daemon, "get_config", lambda: cfg)

    try:
        daemon.run_daemon_forever(poll_seconds=None)
    except KeyboardInterrupt:
        pass

    assert sync_count > maintain_count, (
        f"sync ({sync_count}) should run more often than maintain ({maintain_count})"
    )
