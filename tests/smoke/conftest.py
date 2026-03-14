"""Smoke test fixtures — skip unless LERIM_SMOKE=1."""

from __future__ import annotations

import os

import pytest


def pytest_collection_modifyitems(config, items):
    """Skip smoke tests unless LERIM_SMOKE env var is set."""
    if os.environ.get("LERIM_SMOKE"):
        return
    smoke_dir = os.path.dirname(__file__)
    skip = pytest.mark.skip(reason="LERIM_SMOKE not set")
    for item in items:
        if str(item.fspath).startswith(smoke_dir):
            item.add_marker(skip)
