"""Tests unitaires pour la configuration.

Couvre :
- Settings : defaults, validation, priorités
- get_settings : cache, singleton
- Settings.get_vault_path : priorité LOCAL_VAULT_PATH > VAULT_PATH
- Settings.get_gemini_api_key : priorité GEMINI_API_KEY > GEMINI_API_KEY_2
- Settings.validate_log_level : validation des niveaux de log
"""

import os
from unittest.mock import patch

import pytest

from src.config import Settings, get_settings


class TestSettingsDefaults:
    """Tests pour les valeurs par défaut de Settings."""

    def test_default_vault_path(self):
        s = Settings(vault_path="/test", local_vault_path="")
        assert s.vault_path == "/test"

    def test_default_gemini_model(self):
        s = Settings(vault_path="/test")
        assert s.gemini_model_wiki == "gemini-2.5-flash"

    def test_default_log_level(self):
        s = Settings(vault_path="/test", log_level="INFO")
        assert s.log_level == "INFO"

    def test_default_medium_extract_output(self):
        s = Settings(vault_path="/test")
        assert "medium_extract" in s.medium_extract_output

    def test_default_substack_extract_output(self):
        s = Settings(vault_path="/test")
        assert "substack_extract" in s.substack_extract_output

    def test_extra_fields_ignored(self):
        """Les champs supplémentaires dans .env sont ignorés (extra='ignore')."""
        # This should not raise even with unknown fields
        s = Settings(vault_path="/test", ENABLE_SEMANTIC_SEARCH="true")
        assert s.vault_path == "/test"


class TestSettingsGetVaultPath:
    """Tests pour Settings.get_vault_path."""

    def test_local_vault_path_takes_priority(self):
        s = Settings(vault_path="/vps/path", local_vault_path="/local/path")
        assert s.get_vault_path() == "/local/path"

    def test_vault_path_when_local_empty(self):
        s = Settings(vault_path="/vps/path", local_vault_path="")
        assert s.get_vault_path() == "/vps/path"

    def test_vault_path_when_local_none_like(self):
        s = Settings(vault_path="/vps/path", local_vault_path="")
        assert s.get_vault_path() == "/vps/path"


class TestSettingsGetGeminiApiKey:
    """Tests pour Settings.get_gemini_api_key."""

    def test_gemini_api_key_priority(self):
        s = Settings(vault_path="/test", gemini_api_key="gemini-key", google_api_key="google-key")
        assert s.get_gemini_api_key() == "gemini-key"

    def test_google_api_key_fallback(self):
        s = Settings(vault_path="/test", gemini_api_key="", google_api_key="google-key")
        assert s.get_gemini_api_key() == "google-key"

    def test_env_fallback(self):
        s = Settings(vault_path="/test", gemini_api_key="", google_api_key="")
        with patch.dict(os.environ, {"GEMINI_API_KEY": "env-key"}):
            assert s.get_gemini_api_key() == "env-key"

    def test_google_env_fallback(self):
        s = Settings(vault_path="/test", gemini_api_key="", google_api_key="")
        with patch.dict(os.environ, {"GEMINI_API_KEY": "", "GEMINI_API_KEY_2": "google-env-key"}):
            assert s.get_gemini_api_key() == "google-env-key"

    def test_empty_when_no_keys(self):
        s = Settings(vault_path="/test", gemini_api_key="", google_api_key="")
        with patch.dict(os.environ, {}, clear=True):
            # Remove any env vars that might be set
            for key in ["GEMINI_API_KEY", "GEMINI_API_KEY_2"]:
                os.environ.pop(key, None)
            assert s.get_gemini_api_key() == ""


class TestSettingsValidateLogLevel:
    """Tests pour la validation du niveau de log."""

    def test_valid_levels(self):
        for level in ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]:
            s = Settings(vault_path="/test", log_level=level)
            assert s.log_level == level

    def test_case_insensitive(self):
        s = Settings(vault_path="/test", log_level="info")
        assert s.log_level == "INFO"

    def test_invalid_level_raises(self):
        with pytest.raises(Exception):
            Settings(vault_path="/test", log_level="INVALID")

    def test_warning_level(self):
        s = Settings(vault_path="/test", log_level="warning")
        assert s.log_level == "WARNING"


class TestGetSettings:
    """Tests pour get_settings (caching)."""

    def test_get_settings_returns_settings(self):
        """get_settings retourne une instance Settings."""
        # Clear cache to ensure fresh instance
        get_settings.cache_clear()
        settings = get_settings()
        assert isinstance(settings, Settings)

    def test_get_settings_cached(self):
        """get_settings retourne la même instance (LRU cache)."""
        get_settings.cache_clear()
        s1 = get_settings()
        s2 = get_settings()
        assert s1 is s2
