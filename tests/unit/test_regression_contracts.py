"""Regression and contract stability tests for Lerim runtime schemas."""

from __future__ import annotations


def test_sync_result_contract_fields():
    """SyncResultContract has exactly these fields."""
    from lerim.runtime.agent import SyncResultContract

    expected = {
        "trace_path",
        "memory_root",
        "workspace_root",
        "run_folder",
        "artifacts",
        "counts",
        "written_memory_paths",
        "summary_path",
    }
    assert set(SyncResultContract.model_fields.keys()) == expected


def test_maintain_result_contract_fields():
    """MaintainResultContract has exactly these fields."""
    from lerim.runtime.agent import MaintainResultContract

    expected = {"memory_root", "workspace_root", "run_folder", "artifacts", "counts"}
    assert set(MaintainResultContract.model_fields.keys()) == expected


def test_sync_counts_fields():
    """SyncCounts has add, update, no_op."""
    from lerim.runtime.contracts import SyncCounts

    assert set(SyncCounts.model_fields.keys()) == {"add", "update", "no_op"}


def test_maintain_counts_fields():
    """MaintainCounts has merged, archived, consolidated, decayed, unchanged."""
    from lerim.runtime.contracts import MaintainCounts

    assert set(MaintainCounts.model_fields.keys()) == {
        "merged",
        "archived",
        "consolidated",
        "decayed",
        "unchanged",
    }


def test_memory_candidate_schema_stable():
    """MemoryCandidate has primitive, kind, title, body, confidence, tags."""
    from lerim.memory.schemas import MemoryCandidate

    expected = {"primitive", "kind", "title", "body", "confidence", "tags"}
    assert set(MemoryCandidate.model_fields.keys()) == expected


def test_cli_subcommands_present():
    """CLI parser has all expected subcommands."""
    from lerim.app.cli import build_parser

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
        "daemon",
        "ask",
        "memory",
        "dashboard",
        "status",
    ):
        assert cmd in choices, f"Missing CLI subcommand: {cmd}"


def test_memory_frontmatter_schema_keys():
    """MEMORY_FRONTMATTER_SCHEMA dict has expected keys for each type."""
    from lerim.memory.memory_record import MEMORY_FRONTMATTER_SCHEMA, MemoryType

    assert MemoryType.decision in MEMORY_FRONTMATTER_SCHEMA
    assert MemoryType.learning in MEMORY_FRONTMATTER_SCHEMA
    assert "id" in MEMORY_FRONTMATTER_SCHEMA[MemoryType.decision]
    assert "kind" in MEMORY_FRONTMATTER_SCHEMA[MemoryType.learning]
