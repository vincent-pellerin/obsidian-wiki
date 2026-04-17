"""Configuration de l'application via variables d'environnement."""

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# Déterminer le chemin absolu vers le .env (racine du projet)
# Le fichier config.py est dans src/, donc on remonte d'un niveau
_PROJECT_ROOT = Path(__file__).parent.parent
_ENV_FILE_PATH = _PROJECT_ROOT / ".env"


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
        env_file=str(_ENV_FILE_PATH),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Vault — LOCAL_VAULT_PATH prioritaire si défini (développement local)
    local_vault_path: str = ""
    vault_path: str = "/home/vincent/obsidian-second-brain-vps"

    def get_vault_path(self) -> str:
        """Retourne le chemin du vault adapté à l'environnement.

        Priorité : LOCAL_VAULT_PATH (local) > VAULT_PATH (VPS)
        """
        return self.local_vault_path if self.local_vault_path else self.vault_path

    # Gemini API — supporte GEMINI_API_KEY ou GEMINI_API_KEY_2
    gemini_api_key: str = ""
    google_api_key: str = ""
    # Modèle par défaut : gemini-2.5-flash-lite (pas de thinking tokens = coût prévisible)
    # Alternative avec plus de contexte : gemini-1.5-flash-lite (2M tokens, $0.08/$0.30)
    # Éviter gemini-2.5-flash (thinking tokens actifs par défaut, coût imprévisible)
    gemini_model_wiki: str = "gemini-2.5-flash-lite"

    def get_gemini_api_key(self) -> str:
        """Retourne la clé API Gemini depuis GEMINI_API_KEY_2 uniquement.

        Priorité stricte : GEMINI_API_KEY_2 (variable d'environnement) uniquement.
        Ignore GEMINI_API_KEY et GOOGLE_API_KEY pour éviter les conflits.
        """
        import os
        import logging

        logger = logging.getLogger(__name__)

        # Vérifier si le fichier .env existe
        if not _ENV_FILE_PATH.exists():
            logger.warning(f"Fichier .env non trouvé : {_ENV_FILE_PATH}")
        else:
            logger.debug(f"Fichier .env trouvé : {_ENV_FILE_PATH}")

        # Utiliser UNIQUEMENT GEMINI_API_KEY_2 (variable d'environnement)
        key = os.environ.get("GEMINI_API_KEY_2", "")

        if not key:
            logger.error(
                "GEMINI_API_KEY_2 non trouvée. "
                "Définissez export GEMINI_API_KEY_2=votre_cle dans ~/.zshenv "
                "ou dans les variables d'environnement."
            )
        else:
            # Masquer la clé dans les logs (afficher seulement les 8 derniers caractères)
            masked = "***" + key[-8:] if len(key) > 8 else "***"
            logger.info(f"Clé API GEMINI_API_KEY_2 trouvée (terminaison : {masked})")

        return key

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

    def verify_config(self) -> dict[str, bool | str]:
        """Vérifie la configuration et retourne un rapport.

        Returns:
            Dictionnaire avec l'état de chaque composant critique.
        """
        import os

        return {
            "env_file_exists": _ENV_FILE_PATH.exists(),
            "env_file_path": str(_ENV_FILE_PATH),
            "gemini_api_key_2_env": bool(os.environ.get("GEMINI_API_KEY_2")),
            "gemini_model_wiki": self.gemini_model_wiki,
            "vault_path": self.get_vault_path(),
        }


@lru_cache
def get_settings() -> Settings:
    """Retourne l'instance de configuration (cached).

    Returns:
        Instance Settings initialisée depuis .env.
    """
    return Settings()
