"""Configuration de l'application via variables d'environnement."""

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration globale chargée depuis .env.

    Attributes:
        vault_path: Chemin absolu vers le vault Obsidian.
        gemini_api_key: Clé API Google Gemini.
        gemini_model_wiki: Modèle Gemini pour la compilation wiki.
        medium_extract_output: Répertoire de sortie medium_extract.
        substack_extract_output: Répertoire de sortie substack_extract.
        enable_semantic_search: Activer la recherche sémantique.
        log_level: Niveau de logging (DEBUG, INFO, WARNING, ERROR).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Vault
    vault_path: str = "/home/vincent/obsidian-second-brain-vps"

    # Gemini API
    gemini_api_key: str = ""
    gemini_model_wiki: str = "gemini-2.5-flash-preview-05-20"

    # Bridges
    medium_extract_output: str = "/home/vincent/dev/medium_extract/output"
    substack_extract_output: str = "/home/vincent/dev/substack_extract/output"

    # Features
    enable_semantic_search: bool = False

    # Logging
    log_level: str = "INFO"

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        """Valide que le niveau de log est reconnu."""
        valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in valid_levels:
            raise ValueError(f"LOG_LEVEL invalide : {v}. Valeurs acceptées : {valid_levels}")
        return upper


@lru_cache
def get_settings() -> Settings:
    """Retourne l'instance de configuration (cached).

    Returns:
        Instance Settings initialisée depuis .env.
    """
    return Settings()
