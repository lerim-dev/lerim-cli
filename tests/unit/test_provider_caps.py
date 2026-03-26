"""Tests for the provider capability registry and validation."""

from __future__ import annotations


import pytest

from lerim.runtime.provider_caps import (
	PROVIDER_CAPABILITIES,
	codex_needs_proxy,
	get_missing_api_key_message,
	validate_provider_for_role,
)


class TestValidateProviderForRole:
	"""Tests for validate_provider_for_role."""

	def test_valid_provider_and_role_passes(self):
		"""Known provider + supported role should not raise."""
		validate_provider_for_role("minimax", "lead")

	def test_unknown_provider_raises_with_supported_list(self):
		"""Unknown provider should raise RuntimeError listing all supported providers."""
		with pytest.raises(RuntimeError, match="Unknown provider 'bogus'") as exc_info:
			validate_provider_for_role("bogus", "lead")
		# The error message must list at least some known providers.
		for name in ("minimax", "openai", "ollama"):
			assert name in str(exc_info.value)

	def test_unsupported_role_raises_with_supported_roles(self):
		"""Provider that exists but doesn't support the role should list its supported roles."""
		with pytest.raises(RuntimeError, match="does not support role 'summarize'"):
			validate_provider_for_role("mlx", "summarize")

	def test_blocked_codex_model_raises_with_allowed(self):
		"""Blocked codex model on opencode_go should raise and list allowed models."""
		with pytest.raises(RuntimeError, match="cannot be used for Codex") as exc_info:
			validate_provider_for_role("opencode_go", "codex", model="minimax-m2.7")
		msg = str(exc_info.value)
		assert "kimi-k2.5" in msg
		assert "glm-5" in msg

	def test_non_blocked_codex_model_passes(self):
		"""A codex-compatible model on opencode_go should not raise."""
		validate_provider_for_role("opencode_go", "codex", model="kimi-k2.5")


class TestGetMissingApiKeyMessage:
	"""Tests for get_missing_api_key_message."""

	def test_returns_message_when_key_not_set(self, monkeypatch):
		"""Should return an error string when the required env var is missing."""
		monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
		msg = get_missing_api_key_message("minimax")
		assert msg is not None
		assert "MINIMAX_API_KEY" in msg

	def test_returns_none_when_key_is_set(self, monkeypatch):
		"""Should return None when the required env var is present."""
		monkeypatch.setenv("MINIMAX_API_KEY", "test-key-value")
		assert get_missing_api_key_message("minimax") is None

	def test_returns_none_for_provider_without_key(self):
		"""Providers like ollama have api_key_env=None -- should return None."""
		assert get_missing_api_key_message("ollama") is None


class TestCodexNeedsProxy:
	"""Tests for codex_needs_proxy."""

	def test_true_for_minimax(self):
		assert codex_needs_proxy("minimax") is True

	def test_false_for_openai(self):
		assert codex_needs_proxy("openai") is False


class TestAllProvidersHaveLeadRole:
	"""Every registered provider must support the 'lead' role."""

	@pytest.mark.parametrize("provider", list(PROVIDER_CAPABILITIES.keys()))
	def test_lead_in_roles(self, provider):
		caps = PROVIDER_CAPABILITIES[provider]
		assert "lead" in caps["roles"], f"Provider '{provider}' is missing 'lead' role"
