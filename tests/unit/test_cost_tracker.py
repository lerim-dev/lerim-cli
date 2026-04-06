"""Unit tests for runtime cost tracker: accumulator and DSPy capture."""

from __future__ import annotations

import pytest

from lerim.server.runtime import (
	_run_cost,
	add_cost,
	capture_dspy_cost,
	start_cost_tracking,
	stop_cost_tracking,
)


def _reset():
	"""Ensure cost tracking is off before each test."""
	_run_cost.set(None)


class TestAccumulator:
	"""start/stop/add_cost lifecycle."""

	def setup_method(self):
		"""Reset cost tracking before each test."""
		_reset()

	def teardown_method(self):
		"""Clean up cost tracking after each test."""
		_reset()

	def test_start_stop_zero(self):
		"""Fresh accumulator returns 0.0."""
		start_cost_tracking()
		assert stop_cost_tracking() == 0.0

	def test_add_cost_accumulates(self):
		"""Multiple add_cost calls sum correctly."""
		start_cost_tracking()
		add_cost(0.001)
		add_cost(0.002)
		add_cost(0.0005)
		assert stop_cost_tracking() == pytest.approx(0.0035)

	def test_stop_clears_accumulator(self):
		"""After stop, a new start begins at zero."""
		start_cost_tracking()
		add_cost(1.0)
		stop_cost_tracking()
		start_cost_tracking()
		assert stop_cost_tracking() == 0.0

	def test_add_cost_noop_when_not_tracking(self):
		"""add_cost does nothing when tracking is not active."""
		add_cost(999.0)  # should not raise
		start_cost_tracking()
		assert stop_cost_tracking() == 0.0

	def test_stop_without_start_returns_zero(self):
		"""stop_cost_tracking returns 0.0 when never started."""
		assert stop_cost_tracking() == 0.0


class TestCaptureDspyCost:
	"""capture_dspy_cost reads cost from DSPy LM history."""

	def setup_method(self):
		"""Reset cost tracking before each test."""
		_reset()

	def teardown_method(self):
		"""Clean up cost tracking after each test."""
		_reset()

	def test_captures_cost_from_history(self):
		"""Reads cost from response.usage.cost in LM history entries."""

		class FakeUsage:
			cost = 0.005

		class FakeResponse:
			usage = FakeUsage()

		class FakeLM:
			history = [
				{"response": FakeResponse()},
				{"response": FakeResponse()},
			]

		start_cost_tracking()
		capture_dspy_cost(FakeLM(), history_start=0)
		assert stop_cost_tracking() == pytest.approx(0.01)

	def test_respects_history_start(self):
		"""Only captures cost from entries after history_start."""

		class FakeUsage:
			cost = 0.003

		class FakeResponse:
			usage = FakeUsage()

		class FakeLM:
			history = [
				{"response": FakeResponse()},
				{"response": FakeResponse()},
				{"response": FakeResponse()},
			]

		start_cost_tracking()
		capture_dspy_cost(FakeLM(), history_start=2)
		assert stop_cost_tracking() == pytest.approx(0.003)

	def test_handles_no_history(self):
		"""No-op when LM has no history attribute."""
		start_cost_tracking()
		capture_dspy_cost(object(), history_start=0)
		assert stop_cost_tracking() == 0.0

	def test_handles_dict_usage(self):
		"""Handles usage as a dict (fallback path)."""

		class FakeResponse:
			usage = {"cost": 0.007}

		class FakeLM:
			history = [{"response": FakeResponse()}]

		start_cost_tracking()
		# usage is a dict but getattr(usage, "cost") returns None for dicts,
		# so the fallback dict.get path is exercised
		capture_dspy_cost(FakeLM(), history_start=0)
		assert stop_cost_tracking() == pytest.approx(0.007)

	def test_captures_top_level_cost(self):
		"""Reads cost from top-level entry['cost'] (DSPy >= 2.6 format)."""

		class FakeLM:
			history = [
				{"cost": 0.012, "response": None},
				{"cost": 0.008},
			]

		start_cost_tracking()
		capture_dspy_cost(FakeLM(), history_start=0)
		assert stop_cost_tracking() == pytest.approx(0.02)

	def test_skips_entries_without_response(self):
		"""Entries missing 'response' key are skipped."""

		class FakeLM:
			history = [{"no_response": True}, {"response": None}]

		start_cost_tracking()
		capture_dspy_cost(FakeLM(), history_start=0)
		assert stop_cost_tracking() == 0.0
