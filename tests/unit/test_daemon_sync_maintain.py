"""Test daemon sync and maintain paths, and activity log."""

from __future__ import annotations

from lerim.app import daemon
from lerim.app.activity_log import log_activity
from lerim.config.settings import reload_config
from lerim.sessions import catalog
from tests.helpers import make_config, write_test_config


def _setup(tmp_path, monkeypatch) -> None:
    """Set up test environment with tmp dirs and config."""
    config_path = write_test_config(tmp_path, projects={"testproj": str(tmp_path)})
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
        repo_path=str(tmp_path),
    )

    monkeypatch.setattr(
        "lerim.runtime.runtime.LerimRuntime.sync",
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
    )
    catalog.enqueue_session_job(
        "run-changed-1", session_path=str(session_path), repo_path="/tmp/project"
    )
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
                repo_path=str(tmp_path),
                changed=True,
            )
        ],
    )
    monkeypatch.setattr(
        "lerim.runtime.runtime.LerimRuntime.sync",
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
    """Maintain flow calls LerimAgent.maintain() for each registered project."""
    _setup(tmp_path, monkeypatch)
    called: list[str] = []
    monkeypatch.setattr(
        "lerim.runtime.runtime.LerimRuntime.maintain",
        lambda self, **kw: (
            called.append(kw.get("memory_root", "")),
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
    assert len(called) >= 1
    # Each call should pass an explicit memory_root.
    assert all(r for r in called)


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


def test_daemon_sync_runs_more_often_than_maintain(tmp_path) -> None:
	"""With sync interval < maintain interval, sync fires more often.

	The daemon loop (now inside ``_cmd_serve``) uses
	``sync_interval_minutes`` and ``maintain_interval_minutes`` from
	Config to schedule independent timers.  This test simulates the
	same scheduling logic to verify the invariant without needing the
	full serve infrastructure.
	"""
	import dataclasses

	cfg = dataclasses.replace(
		make_config(tmp_path),
		sync_interval_minutes=1,
		maintain_interval_minutes=5,
	)

	sync_interval = cfg.sync_interval_minutes * 60
	maintain_interval = cfg.maintain_interval_minutes * 60

	# Simulate 20 minutes of wall-clock time
	clock = 0.0
	last_sync = -sync_interval  # fires immediately on first tick
	last_maintain = -maintain_interval  # fires immediately on first tick
	sync_count = 0
	maintain_count = 0

	while clock < 20 * 60:
		if clock - last_sync >= sync_interval:
			sync_count += 1
			last_sync = clock
		if clock - last_maintain >= maintain_interval:
			maintain_count += 1
			last_maintain = clock
		# Advance by the smallest next-due interval
		next_sync = last_sync + sync_interval
		next_maintain = last_maintain + maintain_interval
		clock = max(clock + 1, min(next_sync, next_maintain))

	assert sync_count > maintain_count, (
		f"sync ({sync_count}) should run more often than maintain ({maintain_count})"
	)


def test_log_activity_appends_line(tmp_path, monkeypatch) -> None:
    """log_activity writes one formatted line per call."""
    log_file = tmp_path / "activity.log"
    monkeypatch.setattr("lerim.app.activity_log.ACTIVITY_LOG_PATH", log_file)

    log_activity("sync", "myproject", "3 new, 1 updated, 2 sessions", 4.2)
    log_activity("maintain", "myproject", "2 archived, 1 merged", 6.15)

    lines = log_file.read_text().splitlines()
    assert len(lines) == 2
    assert (
        "| sync     | myproject | 3 new, 1 updated, 2 sessions | $0.0000 | 4.2s"
        in lines[0]
    )
    assert "| maintain | myproject | 2 archived, 1 merged | $0.0000 | 6.2s" in lines[1]
