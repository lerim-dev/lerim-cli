"""Integration test fixtures — skip unless LERIM_INTEGRATION=1."""

from __future__ import annotations

import os

import pytest


def pytest_collection_modifyitems(config, items):
    """Skip all integration tests unless LERIM_INTEGRATION env var is set."""
    if os.environ.get("LERIM_INTEGRATION"):
        return
    skip = pytest.mark.skip(reason="LERIM_INTEGRATION not set")
    for item in items:
        item.add_marker(skip)
