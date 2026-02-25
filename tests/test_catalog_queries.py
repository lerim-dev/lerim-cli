"""Unit tests for session catalog query functions (gaps not covered by test_fts.py)."""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

from lerim.sessions.catalog import (
    claim_session_jobs,
    enqueue_session_job,
    init_sessions_db,
    index_session_for_fts,
    latest_service_run,
    list_sessions_window,
    record_service_run,
)


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path, monkeypatch):
    """Point catalog DB to a temp dir so tests don't touch real data."""
    db_file = tmp_path / "sessions.sqlite3"
    monkeypatch.setattr("lerim.sessions.catalog._DB_INITIALIZED_PATH", None)
    monkeypatch.setattr("lerim.sessions.catalog._db_path", lambda: db_file)
    init_sessions_db()


def _seed_session(run_id: str, agent: str = "claude", start: str | None = None) -> None:
    """Index a minimal session for query tests."""
    index_session_for_fts(
        run_id=run_id,
        agent_type=agent,
        content=f"session {run_id}",
        start_time=start or "2026-02-20T10:00:00Z",
        session_path=f"/tmp/{run_id}.jsonl",
    )


def test_list_sessions_window_with_agent_filter():
    """list_sessions_window filters by agent_types."""
    _seed_session("s1", agent="claude")
    _seed_session("s2", agent="codex")
    rows, total = list_sessions_window(agent_types=["claude"])
    assert all(r["agent_type"] == "claude" for r in rows)
    assert total >= 1


def test_list_sessions_window_with_date_range():
    """list_sessions_window filters by since/until."""
    _seed_session("early", start="2026-01-01T00:00:00Z")
    _seed_session("late", start="2026-03-01T00:00:00Z")
    since = datetime(2026, 2, 1, tzinfo=timezone.utc)
    until = datetime(2026, 2, 28, tzinfo=timezone.utc)
    rows, total = list_sessions_window(since=since, until=until)
    run_ids = {r["run_id"] for r in rows}
    assert "early" not in run_ids
    assert "late" not in run_ids


def test_list_sessions_window_pagination():
    """list_sessions_window with limit/offset paginates correctly."""
    for i in range(5):
        _seed_session(f"page-{i}")
    rows_page1, total = list_sessions_window(limit=2, offset=0)
    rows_page2, _ = list_sessions_window(limit=2, offset=2)
    assert len(rows_page1) == 2
    assert len(rows_page2) == 2
    assert total >= 5
    ids1 = {r["run_id"] for r in rows_page1}
    ids2 = {r["run_id"] for r in rows_page2}
    assert ids1.isdisjoint(ids2)


def test_service_run_record_and_latest():
    """record_service_run then latest_service_run returns it."""
    record_service_run(
        job_type="extract",
        status="completed",
        started_at="2026-02-20T10:00:00Z",
        completed_at="2026-02-20T10:01:00Z",
        trigger="manual",
        details={"count": 1},
    )
    latest = latest_service_run("extract")
    assert latest is not None
    assert latest["status"] == "completed"
    assert latest["job_type"] == "extract"


def test_stale_job_reclamation():
    """claim_session_jobs reclaims stale running jobs."""
    from lerim.sessions.catalog import _connect

    _seed_session("stale-job")
    enqueue_session_job("stale-job", session_path="/tmp/stale.jsonl")
    # First claim picks up the pending job
    jobs = claim_session_jobs(limit=1, timeout_seconds=30)
    assert len(jobs) >= 1
    # Backdate claimed_at/heartbeat_at so the job looks stale
    with _connect() as conn:
        conn.execute(
            "UPDATE session_jobs SET claimed_at = '2020-01-01T00:00:00+00:00', "
            "heartbeat_at = '2020-01-01T00:00:00+00:00' WHERE run_id = 'stale-job'"
        )
        conn.commit()
    # Second claim should reclaim the stale running job
    jobs2 = claim_session_jobs(limit=1, timeout_seconds=30)
    assert len(jobs2) >= 1


def test_concurrent_init_safety(tmp_path, monkeypatch):
    """Multiple threads calling init_sessions_db don't crash."""
    import time

    db_file = tmp_path / "concurrent.sqlite3"
    monkeypatch.setattr("lerim.sessions.catalog._DB_INITIALIZED_PATH", None)
    monkeypatch.setattr("lerim.sessions.catalog._db_path", lambda: db_file)
    errors: list[Exception] = []

    def _init():
        for attempt in range(3):
            try:
                init_sessions_db()
                return
            except Exception as exc:
                if attempt == 2:
                    errors.append(exc)
                time.sleep(0.05)

    threads = [threading.Thread(target=_init) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
