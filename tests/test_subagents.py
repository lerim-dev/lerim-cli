"""Unit tests for the read-only explorer subagent."""

from __future__ import annotations

from tempfile import TemporaryDirectory

from pydantic_ai.models.test import TestModel

from lerim.runtime.contracts import ExplorerEnvelope
from lerim.runtime.subagents import _build_explorer, get_explorer_agent


def test_explorer_has_read_glob_grep():
    """Explorer agent has read, glob, grep tools registered."""
    explorer = _build_explorer(model=TestModel())
    tool_names = set(explorer._function_toolset.tools.keys())
    assert {"glob", "read", "grep"}.issubset(tool_names)


def test_explorer_has_no_write():
    """Explorer agent does NOT have write or edit tools."""
    explorer = _build_explorer(model=TestModel())
    tool_names = set(explorer._function_toolset.tools.keys())
    assert "write" not in tool_names
    assert "edit" not in tool_names


def test_explorer_singleton():
    """get_explorer_agent() returns same instance on repeated calls."""
    import lerim.runtime.subagents as mod

    # Reset singleton to test fresh creation
    mod._explorer_singleton = None
    a = get_explorer_agent()
    b = get_explorer_agent()
    assert a is b


def test_explorer_output_schema():
    """Explorer produces ExplorerEnvelope output."""
    explorer = _build_explorer(model=TestModel())
    assert explorer.output_type is ExplorerEnvelope
