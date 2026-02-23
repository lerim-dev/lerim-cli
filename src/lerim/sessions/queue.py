"""Compatibility facade for queue APIs backed by `lerim.sessions.catalog`."""

from __future__ import annotations

from lerim.sessions.catalog import (
    claim_session_jobs,
    complete_session_job,
    count_session_jobs_by_status,
    enqueue_session_job,
    fail_session_job,
    heartbeat_session_job,
    list_session_jobs,
    latest_service_run,
    record_service_run,
)

__all__ = [
    "enqueue_session_job",
    "claim_session_jobs",
    "heartbeat_session_job",
    "complete_session_job",
    "fail_session_job",
    "list_session_jobs",
    "count_session_jobs_by_status",
    "record_service_run",
    "latest_service_run",
]
