"""Unit tests for the sessions/queue.py compatibility facade.

Verifies that all re-exported names are importable and match the
catalog module originals.
"""

from __future__ import annotations

from lerim.sessions import catalog, queue


def test_all_exports_importable():
    """Every name in queue.__all__ is importable from the module."""
    for name in queue.__all__:
        obj = getattr(queue, name, None)
        assert obj is not None, f"{name} not found in queue module"


def test_exports_match_catalog():
    """Each queue export is the exact same object as the catalog original."""
    for name in queue.__all__:
        queue_obj = getattr(queue, name)
        catalog_obj = getattr(catalog, name)
        assert queue_obj is catalog_obj, (
            f"queue.{name} is not the same object as catalog.{name}"
        )


def test_all_list_complete():
    """__all__ contains all expected queue function names."""
    expected = {
        "enqueue_session_job",
        "claim_session_jobs",
        "heartbeat_session_job",
        "complete_session_job",
        "fail_session_job",
        "list_session_jobs",
        "count_session_jobs_by_status",
        "record_service_run",
        "latest_service_run",
    }
    assert set(queue.__all__) == expected
