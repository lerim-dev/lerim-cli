"""Runtime exports for Lerim orchestration and provider builders.

Uses lazy __getattr__ to avoid circular imports:
runtime.__init__ -> runtime.runtime -> runtime.tools -> memory.extract_pipeline -> ... -> runtime.__init__
"""

from __future__ import annotations

from typing import Any

__all__ = ["LerimRuntime", "SyncAgent", "MaintainAgent", "AskAgent", "build_dspy_lm"]


def __getattr__(name: str) -> Any:
	"""Lazy-load runtime exports to avoid circular import cycles."""
	if name == "LerimRuntime":
		from lerim.runtime.runtime import LerimRuntime

		return LerimRuntime
	if name == "SyncAgent":
		from lerim.runtime.sync_agent import SyncAgent

		return SyncAgent
	if name == "MaintainAgent":
		from lerim.runtime.maintain_agent import MaintainAgent

		return MaintainAgent
	if name == "AskAgent":
		from lerim.runtime.ask_agent import AskAgent

		return AskAgent
	if name == "build_dspy_lm":
		from lerim.runtime.providers import build_dspy_lm

		return build_dspy_lm
	raise AttributeError(name)
