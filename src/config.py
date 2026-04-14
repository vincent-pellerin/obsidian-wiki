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
        log_level: Niveau de logging (DEBUG, INFO, WARNING, ERROR).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Vault — LOCAL_VAULT_PATH prioritaire si défini (développement local)
    local_vault_path: str = ""
    vault_path: str = "/home/vincent/obsidian-second-brain-vps"

    def get_vault_path(self) -> str:
        """Retourne le chemin du vault adapté à l'environnement.

        Priorité : LOCAL_VAULT_PATH (local) > VAULT_PATH (VPS)
        """
        return self.local_vault_path if self.local_vault_path else self.vault_path

    # Gemini API — supporte GEMINI_API_KEY ou GOOGLE_API_KEY
    gemini_api_key: str = ""
    google_api_key: str = ""
    gemini_model_wiki: str = "gemini-2.5-flash"

    def get_gemini_api_key(self) -> str:
        """Retourne la clé API Gemini depuis .env ou variables d'environnement.

        Priorité : GEMINI_API_KEY > GOOGLE_API_KEY
        """
        import os

        return (
            self.gemini_api_key
            or self.google_api_key
            or os.environ.get("GEMINI_API_KEY")
            or os.environ.get("GOOGLE_API_KEY", "")
        )

    # Bridges
    medium_extract_output: str = "/home/vincent/dev/medium_extract/output"
    substack_extract_output: str = "/home/vincent/dev/substack_extract/output"

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
