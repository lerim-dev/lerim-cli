"""Integration test fixtures — skip unless LERIM_INTEGRATION=1.

Provides retry_on_llm_flake for tests that call real LLM APIs and may fail
due to non-deterministic output (e.g. quality gate filters all candidates).
"""

from __future__ import annotations

import functools
import os
import time

import pytest


def pytest_collection_modifyitems(config, items):
	"""Skip integration tests unless LERIM_INTEGRATION env var is set."""
	if os.environ.get("LERIM_INTEGRATION"):
		return
	integration_dir = os.path.dirname(__file__)
	skip = pytest.mark.skip(reason="LERIM_INTEGRATION not set")
	for item in items:
		if str(item.fspath).startswith(integration_dir):
			item.add_marker(skip)


def retry_on_llm_flake(*, max_attempts: int = 3, delay: float = 2.0):
	"""Retry decorator for tests that depend on non-deterministic LLM output.

	Real LLM calls can occasionally return 0 candidates when the quality gate
	filters aggressively or the model is too strict. This retries the test body
	up to *max_attempts* times before raising the final failure.
	"""
	def decorator(fn):
		@functools.wraps(fn)
		def wrapper(*args, **kwargs):
			last_exc = None
			for attempt in range(1, max_attempts + 1):
				try:
					return fn(*args, **kwargs)
				except AssertionError as exc:
					last_exc = exc
					if attempt < max_attempts:
						time.sleep(delay)
			raise last_exc
		return wrapper
	return decorator
