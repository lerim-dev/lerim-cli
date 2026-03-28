"""Tests for the provider capability registry and validation."""

from __future__ import annotations


import pytest

from lerim.runtime.provider_caps import (
	PROVIDER_CAPABILITIES,
	get_missing_api_key_message,
	normalize_model_name,
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


class TestNormalizeModelName:
	"""Tests for normalize_model_name auto-correction."""

	def test_minimax_lowercase_corrected(self):
		"""lowercase minimax-m2.5 with minimax provider -> PascalCase."""
		assert normalize_model_name("minimax", "minimax-m2.5") == "MiniMax-M2.5"

	def test_minimax_correct_casing_unchanged(self):
		"""Already correct PascalCase passes through."""
		assert normalize_model_name("minimax", "MiniMax-M2.5") == "MiniMax-M2.5"

	def test_opencode_go_lowercase_unchanged(self):
		"""opencode_go expects lowercase and gets it back."""
		assert normalize_model_name("opencode_go", "minimax-m2.5") == "minimax-m2.5"

	def test_openrouter_passthrough(self):
		"""Provider without a models list passes through any name."""
		assert normalize_model_name("openrouter", "anything/here") == "anything/here"

	def test_unknown_model_passthrough(self):
		"""Unknown model for a known provider passes through unchanged."""
		assert normalize_model_name("minimax", "custom-model-v3") == "custom-model-v3"

	def test_unknown_provider_passthrough(self):
		"""Completely unknown provider passes through."""
		assert normalize_model_name("bogus", "some-model") == "some-model"

	def test_zai_models_normalized(self):
		"""zai provider models normalize correctly."""
		assert normalize_model_name("zai", "GLM-4.7") == "glm-4.7"
		assert normalize_model_name("zai", "glm-4.5-air") == "glm-4.5-air"


class TestAllProvidersHaveLeadRole:
	"""Every registered provider must support the 'lead' role."""

	@pytest.mark.parametrize("provider", list(PROVIDER_CAPABILITIES.keys()))
	def test_lead_in_roles(self, provider):
		caps = PROVIDER_CAPABILITIES[provider]
		assert "lead" in caps["roles"], f"Provider '{provider}' is missing 'lead' role"
