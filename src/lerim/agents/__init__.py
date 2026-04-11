"""Agent modules: extract (PydanticAI single-pass), maintain, ask + shared tools.

Sync flow uses the PydanticAI single-pass extraction agent in
`lerim.agents.extract.run_extraction`, imported directly by the runtime.
The agent auto-scales its request budget from trace size via
`lerim.agents.tools.compute_request_budget` and manages its own context
via `note()` and `prune()` tools plus three history processors.
Maintain and ask remain DSPy ReAct modules (future migration deferred).
"""

from __future__ import annotations

from typing import Any

__all__ = ["MaintainAgent", "AskAgent"]


def __getattr__(name: str) -> Any:
	"""Lazy-load agent exports to avoid circular import cycles."""
	if name == "MaintainAgent":
		from lerim.agents.maintain import MaintainAgent
		return MaintainAgent
	if name == "AskAgent":
		from lerim.agents.ask import AskAgent
		return AskAgent
	raise AttributeError(name)
