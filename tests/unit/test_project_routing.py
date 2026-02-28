"""Unit tests for per-project memory routing: match_session_project and repo_path fields."""

from __future__ import annotations

from pathlib import Path

from lerim.adapters.base import SessionRecord
from lerim.config.project_scope import match_session_project
from lerim.sessions.catalog import IndexedSession


def test_exact_match(tmp_path):
    """Exact cwd == project path returns a match."""
    projects = {"myproject": str(tmp_path / "repos" / "myproject")}
    cwd = str(tmp_path / "repos" / "myproject")
    result = match_session_project(cwd, projects)
    assert result is not None
    name, path = result
    assert name == "myproject"
    assert path == Path(cwd).resolve()


def test_subdirectory_match(tmp_path):
    """Session cwd inside a project directory matches the project."""
    project_dir = tmp_path / "repos" / "myproject"
    project_dir.mkdir(parents=True)
    projects = {"myproject": str(project_dir)}
    cwd = str(project_dir / "src" / "lib")
    result = match_session_project(cwd, projects)
    assert result is not None
    name, _ = result
    assert name == "myproject"


def test_nested_projects_most_specific_wins(tmp_path):
    """When cwd matches multiple projects, the deepest (most specific) wins."""
    outer = tmp_path / "repos" / "mono"
    inner = tmp_path / "repos" / "mono" / "packages" / "core"
    outer.mkdir(parents=True)
    inner.mkdir(parents=True)
    projects = {
        "mono": str(outer),
        "core": str(inner),
    }
    cwd = str(inner / "src")
    result = match_session_project(cwd, projects)
    assert result is not None
    name, _ = result
    assert name == "core"


def test_no_match_returns_none(tmp_path):
    """cwd outside all registered projects returns None."""
    projects = {"proj": str(tmp_path / "repos" / "proj")}
    cwd = str(tmp_path / "other" / "dir")
    result = match_session_project(cwd, projects)
    assert result is None


def test_none_cwd_returns_none():
    """None session_cwd returns None immediately."""
    result = match_session_project(None, {"proj": "/some/path"})
    assert result is None


def test_empty_projects_returns_none(tmp_path):
    """Empty projects dict means no match."""
    result = match_session_project(str(tmp_path), {})
    assert result is None


def test_session_record_has_repo_path():
    """SessionRecord dataclass exposes repo_path field."""
    rec = SessionRecord(
        run_id="r1",
        agent_type="claude",
        session_path="/tmp/s.jsonl",
        repo_path="/home/user/project",
    )
    assert rec.repo_path == "/home/user/project"


def test_session_record_repo_path_defaults_none():
    """SessionRecord repo_path defaults to None when not provided."""
    rec = SessionRecord(
        run_id="r1",
        agent_type="claude",
        session_path="/tmp/s.jsonl",
    )
    assert rec.repo_path is None


def test_indexed_session_has_repo_path():
    """IndexedSession dataclass exposes repo_path field."""
    idx = IndexedSession(
        run_id="r1",
        agent_type="codex",
        session_path="/tmp/s.jsonl",
        start_time=None,
        repo_path="/home/user/project",
    )
    assert idx.repo_path == "/home/user/project"


def test_indexed_session_repo_path_defaults_none():
    """IndexedSession repo_path defaults to None when not provided."""
    idx = IndexedSession(
        run_id="r1",
        agent_type="codex",
        session_path="/tmp/s.jsonl",
        start_time=None,
    )
    assert idx.repo_path is None
