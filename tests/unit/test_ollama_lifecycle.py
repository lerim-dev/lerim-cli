"""Unit tests for the Ollama model load/unload lifecycle manager.

Tests verify that the context manager correctly loads models on enter,
unloads on exit, handles unreachable servers gracefully, respects
auto_unload config, and deduplicates models across roles.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from lerim.config.settings import AgentRoleConfig, CodexRoleConfig, Config, DSPyRoleConfig
from lerim.runtime.ollama_lifecycle import (
    _ollama_models,
    ollama_lifecycle,
)
from tests.helpers import make_config


def _make_ollama_config(
    tmp_path: Path,
    *,
    lead_provider: str = "ollama",
    lead_model: str = "qwen3.5:4b-q8_0",
    extract_provider: str = "ollama",
    extract_model: str = "qwen3.5:4b-q8_0",
    auto_unload: bool = True,
) -> Config:
    """Build a Config with Ollama roles for testing."""
    base = make_config(tmp_path)
    return Config(
        data_dir=base.data_dir,
        global_data_dir=base.global_data_dir,
        memory_dir=base.memory_dir,
        index_dir=base.index_dir,
        memories_db_path=base.memories_db_path,
        graph_db_path=base.graph_db_path,
        sessions_db_path=base.sessions_db_path,
        platforms_path=base.platforms_path,
        memory_scope=base.memory_scope,
        memory_project_dir_name=base.memory_project_dir_name,
        decay_enabled=base.decay_enabled,
        decay_days=base.decay_days,
        decay_min_confidence_floor=base.decay_min_confidence_floor,
        decay_archive_threshold=base.decay_archive_threshold,
        decay_recent_access_grace_days=base.decay_recent_access_grace_days,
        server_host=base.server_host,
        server_port=base.server_port,
        sync_interval_minutes=base.sync_interval_minutes,
        maintain_interval_minutes=base.maintain_interval_minutes,
        lead_role=AgentRoleConfig(
            provider=lead_provider,
            model=lead_model,
            api_base="",
            fallback_models=(),
            timeout_seconds=300,
            max_iterations=10,
            openrouter_provider_order=(),
        ),
        codex_role=CodexRoleConfig(),
        extract_role=DSPyRoleConfig(
            provider=extract_provider,
            model=extract_model,
            api_base="",
            timeout_seconds=180,
            max_window_tokens=300000,
            window_overlap_tokens=5000,
            openrouter_provider_order=(),
        ),
        summarize_role=DSPyRoleConfig(
            provider=extract_provider,
            model=extract_model,
            api_base="",
            timeout_seconds=180,
            max_window_tokens=300000,
            window_overlap_tokens=5000,
            openrouter_provider_order=(),
        ),
        sync_window_days=7,
        sync_max_sessions=50,
        parallel_pipelines=True,
        tracing_enabled=False,
        tracing_include_httpx=False,
        tracing_include_content=True,
        anthropic_api_key=None,
        openai_api_key=None,
        zai_api_key=None,
        openrouter_api_key=None,
        minimax_api_key=None,
        opencode_api_key=None,
        provider_api_bases={
            "ollama": "http://127.0.0.1:11434",
        },
        auto_unload=auto_unload,
        agents={},
        projects={},
        cloud_endpoint="https://api.lerim.dev",
        cloud_token=None,
    )


class TestOllamaModels:
    """Tests for _ollama_models() model collection."""

    def test_no_ollama_roles(self, tmp_path: Path) -> None:
        """No-op when no roles use ollama provider."""
        config = make_config(tmp_path)
        assert _ollama_models(config) == []

    def test_ollama_roles_deduped(self, tmp_path: Path) -> None:
        """Same model used by multiple roles appears once."""
        config = _make_ollama_config(tmp_path)
        models = _ollama_models(config)
        assert len(models) == 1
        assert models[0] == ("http://127.0.0.1:11434", "qwen3.5:4b-q8_0")

    def test_different_models(self, tmp_path: Path) -> None:
        """Different models across roles produce multiple entries."""
        config = _make_ollama_config(
            tmp_path,
            lead_model="qwen3.5:9b-q8_0",
            extract_model="qwen3.5:4b-q8_0",
        )
        models = _ollama_models(config)
        assert len(models) == 2

    def test_mixed_providers(self, tmp_path: Path) -> None:
        """Only ollama roles are collected, cloud roles ignored."""
        config = _make_ollama_config(
            tmp_path,
            lead_provider="minimax",
            lead_model="MiniMax-M2.5",
            extract_provider="ollama",
            extract_model="qwen3.5:4b-q8_0",
        )
        models = _ollama_models(config)
        assert len(models) == 1
        assert models[0][1] == "qwen3.5:4b-q8_0"


class TestOllamaLifecycle:
    """Tests for the ollama_lifecycle context manager."""

    def test_noop_no_ollama(self, tmp_path: Path) -> None:
        """Context manager is a no-op when no ollama roles configured."""
        config = make_config(tmp_path)
        with ollama_lifecycle(config):
            pass  # Should not make any HTTP calls

    @patch("lerim.runtime.ollama_lifecycle._unload_model")
    @patch("lerim.runtime.ollama_lifecycle._load_model")
    @patch("lerim.runtime.ollama_lifecycle._is_ollama_reachable", return_value=True)
    def test_load_and_unload(
        self,
        mock_reachable: MagicMock,
        mock_load: MagicMock,
        mock_unload: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Model is loaded on enter and unloaded on exit."""
        config = _make_ollama_config(tmp_path)
        with ollama_lifecycle(config):
            mock_load.assert_called_once_with(
                "http://127.0.0.1:11434", "qwen3.5:4b-q8_0"
            )
            mock_unload.assert_not_called()
        mock_unload.assert_called_once_with("http://127.0.0.1:11434", "qwen3.5:4b-q8_0")

    @patch("lerim.runtime.ollama_lifecycle._unload_model")
    @patch("lerim.runtime.ollama_lifecycle._load_model")
    @patch("lerim.runtime.ollama_lifecycle._is_ollama_reachable", return_value=True)
    def test_auto_unload_false_skips_unload(
        self,
        mock_reachable: MagicMock,
        mock_load: MagicMock,
        mock_unload: MagicMock,
        tmp_path: Path,
    ) -> None:
        """When auto_unload=False, model is loaded but not unloaded."""
        config = _make_ollama_config(tmp_path, auto_unload=False)
        with ollama_lifecycle(config):
            pass
        mock_load.assert_called_once()
        mock_unload.assert_not_called()

    @patch("lerim.runtime.ollama_lifecycle._unload_model")
    @patch("lerim.runtime.ollama_lifecycle._load_model")
    @patch("lerim.runtime.ollama_lifecycle._is_ollama_reachable", return_value=False)
    def test_unreachable_skips_gracefully(
        self,
        mock_reachable: MagicMock,
        mock_load: MagicMock,
        mock_unload: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Unreachable Ollama is handled gracefully — no crash."""
        config = _make_ollama_config(tmp_path)
        with ollama_lifecycle(config):
            pass
        mock_load.assert_not_called()
        mock_unload.assert_not_called()

    @patch("lerim.runtime.ollama_lifecycle._unload_model")
    @patch("lerim.runtime.ollama_lifecycle._load_model")
    @patch("lerim.runtime.ollama_lifecycle._is_ollama_reachable", return_value=True)
    def test_unload_on_exception(
        self,
        mock_reachable: MagicMock,
        mock_load: MagicMock,
        mock_unload: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Model is unloaded even when the inner block raises."""
        config = _make_ollama_config(tmp_path)
        with pytest.raises(ValueError, match="test error"):
            with ollama_lifecycle(config):
                raise ValueError("test error")
        mock_unload.assert_called_once()

    @patch("lerim.runtime.ollama_lifecycle._unload_model")
    @patch("lerim.runtime.ollama_lifecycle._load_model")
    @patch("lerim.runtime.ollama_lifecycle._is_ollama_reachable", return_value=True)
    def test_multiple_models(
        self,
        mock_reachable: MagicMock,
        mock_load: MagicMock,
        mock_unload: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Multiple distinct models are each loaded and unloaded."""
        config = _make_ollama_config(
            tmp_path,
            lead_model="qwen3.5:9b-q8_0",
            extract_model="qwen3.5:4b-q8_0",
        )
        with ollama_lifecycle(config):
            assert mock_load.call_count == 2
        assert mock_unload.call_count == 2
