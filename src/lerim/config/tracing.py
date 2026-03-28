"""OpenTelemetry tracing for agent instrumentation.

Sends spans to Logfire cloud (free tier). Activated by ``tracing.enabled = true``
in config or ``LERIM_TRACING=1``.

Instruments three layers:
  1. DSPy extraction/summarisation pipelines (ChainOfThought, Predict)
  2. OpenAI Agents SDK agent loop (LLM calls, tool decisions, tool executions)
  3. Manual spans in daemon / oai_agent / extract_pipeline for orchestration context
"""

from __future__ import annotations

from typing import Any

import logfire
from logfire import ScrubMatch, ScrubbingOptions
from loguru import logger

from lerim.config.settings import Config


def _allow_session_fields(match: ScrubMatch) -> Any:
	"""Allow 'session' pattern through — Lerim sessions are coding sessions, not auth.

	Logfire's default scrubber treats 'session' as sensitive (session tokens,
	session cookies). In Lerim, 'session' refers to coding agent sessions and
	appears in every span attribute and LLM prompt. Scrubbing it makes all
	traces unreadable.
	"""
	if match.pattern_match.group(0).lower() == "session":
		return match.value  # keep original value
	return None  # scrub other patterns (password, api_key, etc.)


def configure_tracing(config: Config) -> None:
	"""Activate OpenTelemetry tracing if enabled in config or via LERIM_TRACING env var.

	Sends traces to Logfire cloud via the token in ``.logfire/`` directory.
	Must be called once at startup before any agent is constructed.
	"""
	if not config.tracing_enabled:
		return

	logfire.configure(
		send_to_logfire="if-token-present",
		service_name="lerim",
		console=False,
		scrubbing=ScrubbingOptions(callback=_allow_session_fields),
	)

	# --- DSPy instrumentation (extraction / summarisation) ---------------
	logfire.instrument_dspy()

	# --- OpenAI Agents SDK instrumentation (agent loop) ------------------
	# Wraps the OAI SDK's TraceProvider so every agent span (LLM call,
	# tool decision, tool execution) becomes an OTel span in Logfire.
	logfire.instrument_openai_agents()

	# Remove the default OAI BatchTraceProcessor → BackendSpanExporter
	# which would otherwise export spans to OpenAI's servers.
	# The Logfire wrapper handles all export via OTel.
	from agents.tracing import set_trace_processors
	set_trace_processors([])

	# --- Optional HTTP instrumentation -----------------------------------
	if config.tracing_include_httpx:
		logfire.instrument_httpx(capture_all=True)

	logger.info("OTel tracing enabled → Logfire (DSPy + OAI Agents)")


if __name__ == "__main__":
    """Minimal self-test: configure_tracing runs without error."""
    from lerim.config.settings import load_config

    cfg = load_config()
    configure_tracing(cfg)
    state = "enabled" if cfg.tracing_enabled else "disabled"
    print(f"tracing.py self-test passed (tracing {state})")
