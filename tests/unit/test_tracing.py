"""Tests for MLflow tracing configuration (DSPy autologging)."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

# Seed sys.modules with mock mlflow before importing the tracing module.
_mock_mlflow = MagicMock()
_mock_mlflow_dspy = MagicMock()
_mock_mlflow.dspy = _mock_mlflow_dspy

if "mlflow" not in sys.modules:
	sys.modules["mlflow"] = _mock_mlflow
	sys.modules["mlflow.dspy"] = _mock_mlflow_dspy

from lerim.config.tracing import configure_tracing  # noqa: E402


def _make_config(*, enabled: bool):
	"""Build a minimal mock Config for tracing tests."""
	cfg = MagicMock()
	cfg.mlflow_enabled = enabled
	return cfg


@patch("lerim.config.tracing.mlflow")
def test_tracing_disabled_does_nothing(mock_mlflow: MagicMock) -> None:
	"""When mlflow_enabled is False, no mlflow calls should be made."""
	configure_tracing(_make_config(enabled=False))
	mock_mlflow.set_experiment.assert_not_called()
	mock_mlflow.dspy.autolog.assert_not_called()


@patch("lerim.config.tracing.mlflow")
def test_tracing_enabled_sets_experiment_and_autologs(
	mock_mlflow: MagicMock,
) -> None:
	"""When mlflow_enabled is True, set_experiment and dspy.autolog are called."""
	configure_tracing(_make_config(enabled=True))
	mock_mlflow.set_experiment.assert_called_once_with("lerim")
	mock_mlflow.dspy.autolog.assert_called_once()
