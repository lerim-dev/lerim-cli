"""Agent modules: extract, maintain, ask + shared tools (all PydanticAI)."""

from __future__ import annotations

from typing import Any

__all__ = ["run_maintain", "run_ask"]


def __getattr__(name: str) -> Any:
	"""Lazy-load agent exports to avoid circular import cycles."""
	if name == "run_maintain":
		from lerim.agents.maintain import run_maintain
		return run_maintain
	if name == "run_ask":
		from lerim.agents.ask import run_ask
		return run_ask
	raise AttributeError(name)
