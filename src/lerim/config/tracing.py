"""OpenTelemetry tracing for PydanticAI agent instrumentation.

Sends spans to Logfire cloud (free tier). Activated by ``tracing.enabled = true``
in config or ``LERIM_TRACING=1``.
"""

from __future__ import annotations

import logfire
from loguru import logger

from lerim.config.settings import Config


def configure_tracing(config: Config) -> None:
    """Activate OpenTelemetry tracing if enabled in config or via LERIM_TRACING env var.

    Sends traces to Logfire cloud via the token in ``.logfire/`` directory.
    Must be called once at startup before any LerimAgent is constructed.
    """
    if not config.tracing_enabled:
        return

    logfire.configure(
        send_to_logfire="if-token-present",
        console=False,
    )
    logfire.instrument_pydantic_ai()
    if config.tracing_include_httpx:
        logfire.instrument_httpx(capture_all=True)

    logger.info("OTel tracing enabled → Logfire")


if __name__ == "__main__":
    """Minimal self-test: configure_tracing runs without error."""
    from lerim.config.settings import load_config

    cfg = load_config()
    configure_tracing(cfg)
    state = "enabled" if cfg.tracing_enabled else "disabled"
    print(f"tracing.py self-test passed (tracing {state})")
