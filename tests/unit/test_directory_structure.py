"""Tests for the clean data directory structure.

Verifies the separation of infrastructure (global ~/.lerim) from
knowledge (per-project <project>/.lerim/memory/).

Per-project .lerim/ should contain ONLY memory/ (with summaries/ and archived/).
Global ~/.lerim/ should contain workspace/, index/, cache/, logs/.
"""

from __future__ import annotations

from pathlib import Path

from lerim.memory.repo import (
	build_memory_paths,
	ensure_global_infrastructure,
	ensure_project_memory,
)


# ---------------------------------------------------------------------------
# Project directory structure: memory only
# ---------------------------------------------------------------------------


def test_project_lerim_contains_only_memory(tmp_path):
	"""Per-project .lerim/ should contain only memory/ after initialization."""
	project_data = tmp_path / "project" / ".lerim"
	project_data.mkdir(parents=True)
	paths = build_memory_paths(project_data)
	ensure_project_memory(paths)

	top_level = {p.name for p in project_data.iterdir()}
	assert top_level == {"memory"}, f"Expected only 'memory', got: {top_level}"


def test_project_memory_has_correct_subdirs(tmp_path):
	"""Per-project memory/ should have summaries/ and archived/."""
	project_data = tmp_path / "project" / ".lerim"
	project_data.mkdir(parents=True)
	paths = build_memory_paths(project_data)
	ensure_project_memory(paths)

	memory_dir = project_data / "memory"
	subdirs = {p.name for p in memory_dir.iterdir() if p.is_dir()}
	assert "summaries" in subdirs
	assert "archived" in subdirs


def test_project_lerim_no_workspace(tmp_path):
	"""Per-project .lerim/ must NOT have workspace/ directory."""
	project_data = tmp_path / "project" / ".lerim"
	project_data.mkdir(parents=True)
	paths = build_memory_paths(project_data)
	ensure_project_memory(paths)

	assert not (project_data / "workspace").exists()


def test_project_lerim_no_index(tmp_path):
	"""Per-project .lerim/ must NOT have index/ directory."""
	project_data = tmp_path / "project" / ".lerim"
	project_data.mkdir(parents=True)
	paths = build_memory_paths(project_data)
	ensure_project_memory(paths)

	assert not (project_data / "index").exists()


def test_project_lerim_no_cache(tmp_path):
	"""Per-project .lerim/ must NOT have cache/ directory."""
	project_data = tmp_path / "project" / ".lerim"
	project_data.mkdir(parents=True)
	paths = build_memory_paths(project_data)
	ensure_project_memory(paths)

	assert not (project_data / "cache").exists()


# ---------------------------------------------------------------------------
# Global directory structure: infrastructure
# ---------------------------------------------------------------------------


def test_global_infrastructure_dirs(tmp_path):
	"""Global ~/.lerim/ should have workspace/, index/, cache/, logs/."""
	ensure_global_infrastructure(tmp_path)

	expected = {"workspace", "index", "cache", "logs"}
	actual = {p.name for p in tmp_path.iterdir() if p.is_dir()}
	assert expected.issubset(actual), f"Missing: {expected - actual}"


def test_global_no_memory_created_by_infrastructure(tmp_path):
	"""ensure_global_infrastructure should NOT create memory/ dir."""
	ensure_global_infrastructure(tmp_path)
	assert not (tmp_path / "memory").exists()


# ---------------------------------------------------------------------------
# Combined: verify full structure after both init calls
# ---------------------------------------------------------------------------

EXPECTED_GLOBAL_DIRS = {"workspace", "index", "cache", "logs"}
FORBIDDEN_PROJECT_DIRS = {"workspace", "index", "cache", "logs", "config.toml"}


def verify_directory_structure(
	global_dir: Path,
	project_dir: Path,
) -> list[str]:
	"""Verify both directories match the expected clean structure.

	Returns a list of issues found (empty = pass).
	"""
	issues: list[str] = []

	# Global checks
	for name in EXPECTED_GLOBAL_DIRS:
		if not (global_dir / name).is_dir():
			issues.append(f"Global missing: {name}/")

	# Project checks: must have memory/
	if not (project_dir / "memory").is_dir():
		issues.append("Project missing: memory/")
	if not (project_dir / "memory" / "summaries").is_dir():
		issues.append("Project missing: memory/summaries/")
	if not (project_dir / "memory" / "archived").is_dir():
		issues.append("Project missing: memory/archived/")

	# Project must NOT have infrastructure dirs
	for name in FORBIDDEN_PROJECT_DIRS:
		path = project_dir / name
		if path.exists():
			issues.append(f"Project has forbidden: {name}")

	return issues


def test_full_structure_verification(tmp_path):
	"""After both init calls, the structure should be clean."""
	global_dir = tmp_path / "global"
	global_dir.mkdir()
	project_dir = tmp_path / "project"
	project_dir.mkdir()

	ensure_global_infrastructure(global_dir)
	ensure_project_memory(build_memory_paths(project_dir))

	issues = verify_directory_structure(global_dir, project_dir)
	assert issues == [], f"Structure issues: {issues}"
