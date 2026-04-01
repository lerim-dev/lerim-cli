"""Track LLM cost per run via OpenRouter's usage.cost response field.

Uses a ContextVar with a mutable accumulator shared by reference so
cost captured in tool calls propagates back to the caller.  Capture path:
 - DSPy: explicit capture_dspy_cost() reads LM history after synchronous
   pipeline calls and adds any reported cost to the accumulator.
"""

from __future__ import annotations

from contextvars import ContextVar


class _Acc:
	"""Mutable cost accumulator shared by reference across context copies."""

	__slots__ = ("total",)

	def __init__(self) -> None:
		self.total = 0.0


_run_cost: ContextVar[_Acc | None] = ContextVar("lerim_run_cost", default=None)


def start_cost_tracking() -> None:
	"""Begin accumulating LLM cost for the current run."""
	_run_cost.set(_Acc())


def stop_cost_tracking() -> float:
	"""Stop tracking and return accumulated cost in USD."""
	acc = _run_cost.get(None)
	cost = acc.total if acc else 0.0
	_run_cost.set(None)
	return cost


def add_cost(amount: float) -> None:
	"""Add cost to the current run's accumulator (no-op when tracking inactive)."""
	acc = _run_cost.get(None)
	if acc is not None:
		acc.total += amount


# ---------------------------------------------------------------------------
# DSPy path: read cost from LM history after pipeline calls
# ---------------------------------------------------------------------------


def capture_dspy_cost(lm: object, history_start: int) -> None:
	"""Add cost from DSPy LM history entries added since *history_start*."""
	history = getattr(lm, "history", None)
	if not isinstance(history, list):
		return
	for entry in history[history_start:]:
		if not isinstance(entry, dict):
			continue
		response = entry.get("response")
		if response is None:
			continue
		usage = getattr(response, "usage", None)
		if usage is None:
			continue
		cost = getattr(usage, "cost", None)
		if cost is None and isinstance(usage, dict):
			cost = usage.get("cost")
		if cost is not None:
			add_cost(float(cost))
