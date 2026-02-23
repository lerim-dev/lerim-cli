"""Typed runtime contracts for PydanticAI orchestration and tool envelopes."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SyncCounts(BaseModel):
    """Stable sync count payload contract."""

    add: int = 0
    update: int = 0
    no_op: int = 0


class MaintainCounts(BaseModel):
    """Stable maintain count payload contract."""

    merged: int = 0
    archived: int = 0
    consolidated: int = 0
    decayed: int = 0
    unchanged: int = 0


class ExplorerItem(BaseModel):
    """Single explorer evidence item returned by read-only subagent."""

    candidate_id: str = ""
    action_hint: str = ""
    matched_file: str = ""
    evidence: str = ""
    snippet: str = ""
    line: int | None = None


class ExplorerEnvelope(BaseModel):
    """Explorer subagent output envelope."""

    items: list[ExplorerItem] = Field(default_factory=list)


if __name__ == "__main__":
    """Run contract model smoke checks."""
    sync = SyncCounts(add=1, update=2, no_op=3)
    assert sync.model_dump() == {"add": 1, "update": 2, "no_op": 3}

    maintain = MaintainCounts(merged=1, archived=2, consolidated=3, decayed=4)
    assert maintain.unchanged == 0

    explorer = ExplorerItem(candidate_id="test", matched_file="/tmp/test.md")
    assert explorer.line is None

    print("runtime contracts: self-test passed")
