"""E2E test fixtures — skip unless LERIM_E2E=1."""

from __future__ import annotations

import os

import pytest


def pytest_collection_modifyitems(config, items):
    """Skip e2e tests unless LERIM_E2E env var is set."""
    if os.environ.get("LERIM_E2E"):
        return
    e2e_dir = os.path.dirname(__file__)
    skip = pytest.mark.skip(reason="LERIM_E2E not set")
    for item in items:
        if str(item.fspath).startswith(e2e_dir):
            item.add_marker(skip)
