"""Regression and contract stability tests for Lerim runtime schemas."""

from __future__ import annotations


def test_sync_result_contract_fields():
    """SyncResultContract has exactly these fields."""
    from lerim.agents.contracts import SyncResultContract

    expected = {
        "trace_path",
        "memory_root",
        "workspace_root",
        "run_folder",
        "artifacts",
        "cost_usd",
    }
    assert set(SyncResultContract.model_fields.keys()) == expected


def test_maintain_result_contract_fields():
    """MaintainResultContract has exactly these fields."""
    from lerim.agents.contracts import MaintainResultContract

    expected = {
        "memory_root",
        "workspace_root",
        "run_folder",
        "artifacts",
        "cost_usd",
    }
    assert set(MaintainResultContract.model_fields.keys()) == expected


def test_cli_subcommands_present():
    """CLI parser has all expected subcommands."""
    from lerim.server.cli import build_parser

    parser = build_parser()
    # Extract subcommand names from the parser
    subparsers_actions = [
        a for a in parser._subparsers._actions if hasattr(a, "_parser_class")
    ]
    choices: set[str] = set()
    for action in subparsers_actions:
        if hasattr(action, "choices") and action.choices:
            choices.update(action.choices.keys())
    for cmd in (
        "connect",
        "sync",
        "maintain",
        "serve",
        "ask",
        "memory",
        "dashboard",
        "status",
        "queue",
        "retry",
        "skip",
    ):
        assert cmd in choices, f"Missing CLI subcommand: {cmd}"


