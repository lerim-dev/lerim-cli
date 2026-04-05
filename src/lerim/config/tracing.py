"""MLflow tracing for DSPy agent observability.

Activates MLflow autologging for DSPy when ``LERIM_MLFLOW=true`` is set.
All DSPy module calls, LM interactions, and tool invocations are captured
automatically — no manual span instrumentation needed.
"""

from __future__ import annotations

import mlflow
import mlflow.dspy
from loguru import logger

from lerim.config.settings import Config


def configure_tracing(config: Config) -> None:
	"""Activate MLflow DSPy autologging if enabled via LERIM_MLFLOW env var.

	Must be called once at startup before any agent is constructed.
	"""
	if not config.mlflow_enabled:
		return

	mlflow.set_tracking_uri(str(config.global_data_dir / "mlruns"))
	mlflow.set_experiment("lerim")
	mlflow.dspy.autolog()
	logger.info("MLflow tracing enabled (DSPy autolog) → {}", config.global_data_dir / "mlruns")


if __name__ == "__main__":
	"""Minimal self-test: configure_tracing runs without error."""
	from lerim.config.settings import load_config

	cfg = load_config()
	configure_tracing(cfg)
	state = "enabled" if cfg.mlflow_enabled else "disabled"
	print(f"tracing.py self-test passed (mlflow {state})")
