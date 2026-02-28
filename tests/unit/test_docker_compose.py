"""Tests for Docker compose generation and GHCR image publishing support.

Verifies that _generate_compose_yml produces correct image/build directives,
that API key values never leak into compose content, and that api_up handles
Docker-unavailable and missing-Dockerfile scenarios gracefully.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lerim import __version__
from lerim.app.api import (
    GHCR_IMAGE,
    _generate_compose_yml,
    api_up,
)
from tests.helpers import make_config


@pytest.fixture(autouse=True)
def _isolate_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Monkeypatch reload_config so compose generation uses a temp config."""
    cfg = make_config(tmp_path)
    monkeypatch.setattr("lerim.app.api.reload_config", lambda: cfg)


def test_default_compose_uses_ghcr_image() -> None:
    """Default compose (build_local=False) emits an image directive with GHCR."""
    content = _generate_compose_yml(build_local=False)
    assert f"image: {GHCR_IMAGE}:" in content
    assert "build:" not in content


def test_build_local_uses_build_directive(monkeypatch: pytest.MonkeyPatch) -> None:
    """build_local=True emits a build directive instead of an image directive."""
    fake_root = Path("/fake/lerim-root")
    monkeypatch.setattr("lerim.app.api._find_package_root", lambda: fake_root)
    content = _generate_compose_yml(build_local=True)
    assert f"build: {fake_root}" in content
    assert "image:" not in content


def test_build_local_no_dockerfile_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """build_local=True raises FileNotFoundError when Dockerfile is missing."""
    monkeypatch.setattr("lerim.app.api._find_package_root", lambda: None)
    with pytest.raises(FileNotFoundError, match="Cannot find Dockerfile"):
        _generate_compose_yml(build_local=True)


def test_no_api_key_values_in_compose(monkeypatch: pytest.MonkeyPatch) -> None:
    """API key values from the environment must not appear in compose content."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-secret-key-12345")
    content = _generate_compose_yml(build_local=False)
    assert "sk-secret-key-12345" not in content


def test_version_tag_matches_dunder_version() -> None:
    """The image tag in the compose file matches the package __version__."""
    content = _generate_compose_yml(build_local=False)
    expected = f"{GHCR_IMAGE}:{__version__}"
    assert expected in content


def test_api_up_docker_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """api_up returns an error dict when Docker is not available."""
    monkeypatch.setattr("lerim.app.api.docker_available", lambda: False)
    result = api_up()
    assert "error" in result
    assert "Docker" in result["error"]


def test_api_up_build_local_no_dockerfile(monkeypatch: pytest.MonkeyPatch) -> None:
    """api_up(build_local=True) returns error dict when Dockerfile is missing."""
    monkeypatch.setattr("lerim.app.api.docker_available", lambda: True)
    monkeypatch.setattr("lerim.app.api._find_package_root", lambda: None)
    result = api_up(build_local=True)
    assert "error" in result
    assert "Dockerfile" in result["error"]
