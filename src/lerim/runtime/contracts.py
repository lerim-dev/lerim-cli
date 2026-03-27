"""Typed runtime contracts for orchestration."""

from __future__ import annotations

from pydantic import BaseModel


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


if __name__ == "__main__":
	"""Run contract model smoke checks."""
	sync = SyncCounts(add=1, update=2, no_op=3)
	assert sync.model_dump() == {"add": 1, "update": 2, "no_op": 3}

	maintain = MaintainCounts(merged=1, archived=2, consolidated=3, decayed=4)
	assert maintain.unchanged == 0

	print("runtime contracts: self-test passed")
