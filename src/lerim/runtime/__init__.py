"""Runtime exports for Lerim orchestration and provider builders.

Uses lazy __getattr__ to avoid circular imports:
runtime.__init__ -> runtime.oai_agent -> runtime.tools -> memory.extract_pipeline -> ... -> runtime.__init__
"""

from __future__ import annotations

from typing import Any

__all__ = ["LerimOAIAgent", "build_dspy_lm", "build_oai_model", "build_oai_context", "build_oai_model_from_role", "build_oai_fallback_models"]


def __getattr__(name: str) -> Any:
	"""Lazy-load runtime exports to avoid circular import cycles."""
	if name == "LerimOAIAgent":
		from lerim.runtime.oai_agent import LerimOAIAgent

		return LerimOAIAgent
	if name == "build_dspy_lm":
		from lerim.runtime.providers import build_dspy_lm

		return build_dspy_lm
	if name == "build_oai_model":
		from lerim.runtime.oai_providers import build_oai_model

		return build_oai_model
	raise AttributeError(name)
