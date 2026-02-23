"""test session extract writeback."""

from __future__ import annotations

import json

from lerim.config.settings import reload_config
from lerim.sessions import catalog
from tests.helpers import write_test_config


def _setup_env(tmp_path, monkeypatch):
    config_path = write_test_config(tmp_path)
    monkeypatch.setenv("LERIM_CONFIG", str(config_path))
    reload_config()
    catalog.init_sessions_db()


def test_update_session_writeback_fields(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    catalog.index_session_for_fts(
        run_id="run-1",
        agent_type="codex",
        content="initial content",
    )

    ok = catalog.update_session_extract_fields(
        "run-1",
        summary_text="short summary",
        tags='["fix-bug","queue"]',
        outcome="mostly_achieved",
    )
    assert ok is True

    row = catalog.fetch_session_doc("run-1")
    assert row is not None
    assert row["summary_text"] == "short summary"
    assert row["tags"] == '["fix-bug","queue"]'
    assert row["outcome"] == "mostly_achieved"


def test_session_extract_writeback_accepts_tag_updates(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    run_id = "run-tags-1"
    catalog.index_session_for_fts(
        run_id=run_id,
        agent_type="codex",
        repo_name="lerim",
        content="initial content",
        summary_text="brief",
    )

    ok = catalog.update_session_extract_fields(
        run_id,
        tags='["agent:codex","repo:lerim"]',
        outcome="completed",
    )
    assert ok is True

    row = catalog.fetch_session_doc(run_id)
    assert row is not None
    tags = json.loads(row["tags"] or "[]")
    assert "agent:codex" in tags
    assert "repo:lerim" in tags
