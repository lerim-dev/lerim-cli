"""Integration test fixtures — skip unless LERIM_INTEGRATION=1."""

from __future__ import annotations

import os

import pytest


def pytest_collection_modifyitems(config, items):
    """Skip integration tests unless LERIM_INTEGRATION env var is set."""
    if os.environ.get("LERIM_INTEGRATION"):
        return
    integration_dir = os.path.dirname(__file__)
    skip = pytest.mark.skip(reason="LERIM_INTEGRATION not set")
    for item in items:
        if str(item.fspath).startswith(integration_dir):
            item.add_marker(skip)
