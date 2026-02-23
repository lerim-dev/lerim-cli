"""test learning runs."""

from __future__ import annotations

from lerim.config.settings import reload_config
from lerim.sessions import catalog
from tests.helpers import write_test_config


def _setup(tmp_path, monkeypatch):
    config_path = write_test_config(tmp_path)
    monkeypatch.setenv("LERIM_CONFIG", str(config_path))
    reload_config()
    catalog.init_sessions_db()


def test_queue_lifecycle_complete(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    enq = catalog.enqueue_session_job(
        "run-1", agent_type="codex", session_path="/tmp/run-1.jsonl"
    )
    assert enq is True

    claimed = catalog.claim_session_jobs(limit=1)
    assert len(claimed) == 1
    assert claimed[0]["run_id"] == "run-1"
    assert claimed[0]["status"] == "running"

    done = catalog.complete_session_job("run-1")
    assert done is True

    counts = catalog.count_session_jobs_by_status()
    assert counts["done"] == 1


def test_queue_fail_to_dead_letter(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    catalog.enqueue_session_job("run-2", max_attempts=1)
    claimed = catalog.claim_session_jobs(limit=1)
    assert claimed[0]["run_id"] == "run-2"

    failed = catalog.fail_session_job("run-2", error="boom", retry_backoff_seconds=1)
    assert failed is True

    rows = catalog.list_session_jobs(limit=5)
    row = next(item for item in rows if item["run_id"] == "run-2")
    assert row["status"] == "dead_letter"


def test_queue_heartbeat_updates_running_job(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    catalog.enqueue_session_job(
        "run-3", agent_type="claude", session_path="/tmp/run-3.jsonl"
    )
    claimed = catalog.claim_session_jobs(limit=1)
    assert claimed and claimed[0]["run_id"] == "run-3"
    first_heartbeat = str(claimed[0].get("heartbeat_at") or "")
    assert first_heartbeat

    touched = catalog.heartbeat_session_job("run-3")
    assert touched is True

    row = next(
        item
        for item in catalog.list_session_jobs(limit=10)
        if item["run_id"] == "run-3"
    )
    second_heartbeat = str(row.get("heartbeat_at") or "")
    assert second_heartbeat
    assert second_heartbeat >= first_heartbeat


def test_queue_fail_marks_failed_before_dead_letter(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    catalog.enqueue_session_job("run-4", max_attempts=3)
    claimed = catalog.claim_session_jobs(limit=1)
    assert claimed and claimed[0]["run_id"] == "run-4"

    failed = catalog.fail_session_job(
        "run-4", error="temporary", retry_backoff_seconds=30
    )
    assert failed is True

    row = next(
        item
        for item in catalog.list_session_jobs(limit=10)
        if item["run_id"] == "run-4"
    )
    assert row["status"] == "failed"


def test_claim_newest_first(tmp_path, monkeypatch):
    """Jobs with newer start_time should be claimed first."""
    _setup(tmp_path, monkeypatch)
    catalog.enqueue_session_job("old-run", start_time="2026-01-01T00:00:00+00:00")
    catalog.enqueue_session_job("new-run", start_time="2026-02-20T12:00:00+00:00")
    catalog.enqueue_session_job("mid-run", start_time="2026-01-15T00:00:00+00:00")

    claimed = catalog.claim_session_jobs(limit=1)
    assert len(claimed) == 1
    assert claimed[0]["run_id"] == "new-run", "should claim newest session first"

    claimed2 = catalog.claim_session_jobs(limit=1)
    assert claimed2[0]["run_id"] == "mid-run", "second claim should get middle session"
