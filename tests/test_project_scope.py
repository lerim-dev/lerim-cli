"""Unit tests for project/global data directory resolution."""

from __future__ import annotations

from pathlib import Path

from lerim.config.project_scope import git_root_for, resolve_data_dirs


def test_git_root_detection(tmp_path):
    """git_root_for finds .git ancestor."""
    (tmp_path / ".git").mkdir()
    sub = tmp_path / "a" / "b"
    sub.mkdir(parents=True)
    result = git_root_for(sub)
    assert result == tmp_path


def test_git_root_none_outside_repo(tmp_path):
    """git_root_for returns None when no .git found."""
    isolated = tmp_path / "no-repo"
    isolated.mkdir()
    result = git_root_for(isolated)
    assert result is None


def test_resolve_global_only(tmp_path):
    """scope='global_only' -> only global dir."""
    global_dir = tmp_path / "global"
    global_dir.mkdir()
    res = resolve_data_dirs(
        scope="global_only",
        project_dir_name=".lerim",
        global_data_dir=global_dir,
    )
    assert len(res.ordered_data_dirs) == 1
    assert res.ordered_data_dirs[0] == global_dir.resolve()


def test_resolve_project_only(tmp_path):
    """scope='project_only' -> only project dir."""
    (tmp_path / ".git").mkdir()
    global_dir = tmp_path / "global"
    global_dir.mkdir()
    res = resolve_data_dirs(
        scope="project_only",
        project_dir_name=".lerim",
        global_data_dir=global_dir,
        repo_path=tmp_path,
    )
    assert len(res.ordered_data_dirs) == 1
    project_lerim = (tmp_path / ".lerim").resolve()
    assert res.ordered_data_dirs[0] == project_lerim


def test_resolve_project_fallback_global(tmp_path):
    """scope='project_fallback_global' -> project first, global second."""
    (tmp_path / ".git").mkdir()
    global_dir = tmp_path / "global"
    global_dir.mkdir()
    res = resolve_data_dirs(
        scope="project_fallback_global",
        project_dir_name=".lerim",
        global_data_dir=global_dir,
        repo_path=tmp_path,
    )
    assert len(res.ordered_data_dirs) == 2
    assert res.ordered_data_dirs[0] == (tmp_path / ".lerim").resolve()
    assert res.ordered_data_dirs[1] == global_dir.resolve()


def test_resolve_deduplication(tmp_path):
    """When project and global point to same dir, no duplicates."""
    (tmp_path / ".git").mkdir()
    # Set global to the same as what project .lerim would resolve to
    project_lerim = tmp_path / ".lerim"
    project_lerim.mkdir()
    res = resolve_data_dirs(
        scope="project_fallback_global",
        project_dir_name=".lerim",
        global_data_dir=project_lerim,
        repo_path=tmp_path,
    )
    assert len(res.ordered_data_dirs) == 1
