"""Central logging configuration using loguru.

Minimal stderr output for human readability. Detailed agent/LLM tracing
is handled by OpenTelemetry (see tracing.py), not by log lines.
"""

from __future__ import annotations

import logging
import os
import sys

from loguru import logger as _BASE_LOGGER


_logger = _BASE_LOGGER

_TRUE_VALUES = {"1", "true", "yes", "on"}


def _env_flag(name: str, default: bool) -> bool:
    """Return boolean environment flag with common truthy values."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in _TRUE_VALUES


_FORMAT = "<green>{time:HH:mm:ss}</green> | <level>{level:<8}</level> | <level>{message}</level>\n"


def _log_filter(record: dict) -> bool:
    """Hide noisy third-party SDK logs unless explicitly opted in."""
    logger_name = str(record.get("name") or "")
    if logger_name.startswith("openai"):
        return _env_flag("LERIM_LOG_OPENAI_HTTP", default=False)
    if logger_name in ("asyncio", "httpx", "httpcore"):
        return False
    message = str(record.get("message") or "")
    if "Using bundled Claude Code CLI:" in message:
        return _env_flag("LERIM_LOG_CLAUDE_SDK", default=False)
    return True


class _InterceptHandler(logging.Handler):
    """Forward stdlib logging records to loguru."""

    def emit(self, record: logging.LogRecord) -> None:
        """Forward one stdlib log record into loguru."""
        try:
            level = _logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        _logger.opt(exception=record.exc_info).log(level, record.getMessage())


def configure_logging(level: str | None = None) -> None:
    """Configure loguru and capture stdlib logging."""
    global _logger
    level = level or os.getenv("LERIM_LOG_LEVEL", "INFO")
    colorize = _env_flag("LERIM_LOG_COLOR", default=sys.stderr.isatty())

    _BASE_LOGGER.remove()
    _logger = _BASE_LOGGER
    _logger.add(
        sys.stderr,
        level=level,
        format=_FORMAT,
        filter=_log_filter,
        colorize=colorize,
        backtrace=False,
        diagnose=False,
    )

    logging.basicConfig(handlers=[_InterceptHandler()], level=0)

    for name in list(logging.root.manager.loggerDict.keys()):
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True

    # Silence noisy third-party loggers
    for noisy in ("LiteLLM", "litellm", "httpx", "httpcore", "openai", "dspy"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


configure_logging()

logger = _logger

__all__ = ["logger", "configure_logging"]


if __name__ == "__main__":
    configure_logging(level="INFO")
    logger.info("config.logging self-test passed")
