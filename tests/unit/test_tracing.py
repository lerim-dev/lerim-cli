"""Tests for OpenTelemetry tracing configuration (logfire instrumentation)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from lerim.config.tracing import configure_tracing


def _make_config(
    *, enabled: bool, include_httpx: bool = False, include_content: bool = True
):
    """Build a minimal mock Config for tracing tests."""
    cfg = MagicMock()
    cfg.tracing_enabled = enabled
    cfg.tracing_include_httpx = include_httpx
    cfg.tracing_include_content = include_content
    return cfg


@patch("lerim.config.tracing.logfire")
def test_tracing_disabled_does_nothing(mock_logfire: MagicMock) -> None:
    """When tracing is disabled, no logfire calls should be made."""
    configure_tracing(_make_config(enabled=False))
    mock_logfire.configure.assert_not_called()
    mock_logfire.instrument_dspy.assert_not_called()
    mock_logfire.instrument_httpx.assert_not_called()


@patch("lerim.config.tracing.logfire")
def test_tracing_enabled_configures_logfire(mock_logfire: MagicMock) -> None:
    """When tracing is enabled, logfire.configure is called with service_name='lerim'."""
    configure_tracing(_make_config(enabled=True))
    mock_logfire.configure.assert_called_once_with(
        send_to_logfire="if-token-present",
        service_name="lerim",
        console=False,
    )


@patch("lerim.config.tracing.logfire")
def test_tracing_enabled_instruments_dspy(mock_logfire: MagicMock) -> None:
    """instrument_dspy is always called when tracing is enabled."""
    configure_tracing(_make_config(enabled=True))
    mock_logfire.instrument_dspy.assert_called_once()


@patch("lerim.config.tracing.logfire")
def test_tracing_httpx_off_by_default(mock_logfire: MagicMock) -> None:
    """instrument_httpx is NOT called when include_httpx is False."""
    configure_tracing(_make_config(enabled=True, include_httpx=False))
    mock_logfire.instrument_httpx.assert_not_called()


@patch("lerim.config.tracing.logfire")
def test_tracing_httpx_on_when_configured(mock_logfire: MagicMock) -> None:
    """instrument_httpx IS called with capture_all=True when include_httpx is True."""
    configure_tracing(_make_config(enabled=True, include_httpx=True))
    mock_logfire.instrument_httpx.assert_called_once_with(capture_all=True)
