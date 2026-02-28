"""test fts."""

from __future__ import annotations

from pathlib import Path

from lerim.config.settings import reload_config
from lerim.sessions import catalog
from tests.helpers import write_test_config


def _setup_env(tmp_path: Path, monkeypatch) -> None:
    config_path = write_test_config(tmp_path)
    monkeypatch.setenv("LERIM_CONFIG", str(config_path))
    reload_config()


def test_index_and_count_sessions(tmp_path: Path, monkeypatch) -> None:
    _setup_env(tmp_path, monkeypatch)
    catalog.init_sessions_db()

    ok = catalog.index_session_for_fts(
        run_id="run-1",
        agent_type="codex",
        content="fix parser crash and rerun tests",
        repo_name="repo-a",
        start_time="2026-02-14T00:00:00+00:00",
        summaries='["fixed parser"]',
    )
    assert ok is True
    assert catalog.count_fts_indexed() == 1

    row = catalog.fetch_session_doc("run-1")
    assert row is not None
    assert row["agent_type"] == "codex"
    assert row["repo_name"] == "repo-a"


def test_update_session_extract_fields(tmp_path: Path, monkeypatch) -> None:
    _setup_env(tmp_path, monkeypatch)
    catalog.init_sessions_db()
    catalog.index_session_for_fts(
        run_id="run-2",
        agent_type="claude",
        content="implement queue retry",
        repo_name="repo-b",
        start_time="2026-02-14T00:00:00+00:00",
    )

    updated = catalog.update_session_extract_fields(
        "run-2",
        summary_text="implemented queue retry",
        tags='["queue","retry"]',
        outcome="fully_achieved",
    )
    assert updated is True

    row = catalog.fetch_session_doc("run-2")
    assert row is not None
    assert row["summary_text"] == "implemented queue retry"
    assert row["outcome"] == "fully_achieved"
