"""Bridge Substack → vault Obsidian.

Copie les posts et newsletters extraits depuis substack_extract/output/
vers 00_RAW/articles/substack/ du vault, avec déduplication.
"""

import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import frontmatter

from src.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class SubstackSyncResult:
    """Résultat d'une synchronisation Substack.

    Attributes:
        posts_synced: Posts copiés avec succès.
        posts_skipped: Posts déjà présents (dédupliqués).
        newsletters_synced: Newsletters copiées.
        newsletters_skipped: Newsletters déjà présentes.
        errors: Fichiers ayant échoué avec leur message d'erreur.
    """

    posts_synced: list[Path] = field(default_factory=list)
    posts_skipped: list[Path] = field(default_factory=list)
    newsletters_synced: list[Path] = field(default_factory=list)
    newsletters_skipped: list[Path] = field(default_factory=list)
    errors: list[tuple[Path, str]] = field(default_factory=list)

    @property
    def total_synced(self) -> int:
        """Nombre total de fichiers copiés."""
        return len(self.posts_synced) + len(self.newsletters_synced)

    @property
    def total_skipped(self) -> int:
        """Nombre total de fichiers ignorés."""
        return len(self.posts_skipped) + len(self.newsletters_skipped)

    def summary(self) -> str:
        """Résumé lisible du résultat."""
        return (
            f"SubstackBridge sync: "
            f"{len(self.posts_synced)} posts + {len(self.newsletters_synced)} newsletters copiés, "
            f"{self.total_skipped} ignorés, {len(self.errors)} erreurs"
        )


class SubstackBridge:
    """Pont entre substack_extract et le vault Obsidian.

    Synchronise les posts et newsletters markdown depuis le répertoire
    de sortie de substack_extract vers 00_RAW/articles/substack/ du vault.

    Attributes:
        source_dir: Répertoire source (substack_extract/output/).
        dest_dir: Répertoire destination (vault/00_RAW/articles/substack/).
    """

    def __init__(self) -> None:
        """Initialise le bridge avec la configuration courante."""
        settings = get_settings()
        self.source_dir = Path(settings.substack_extract_output)
        self.dest_dir = Path(settings.get_vault_path()) / "00_RAW" / "articles" / "substack"

    def _is_newsletter(self, file_path: Path) -> bool:
        """Détermine si un fichier est une newsletter basé sur son chemin.

        Args:
            file_path: Chemin du fichier à analyser.

        Returns:
            True si le fichier est dans un répertoire newsletters/.
        """
        return "newsletters" in file_path.parts

    def sync_all(self, *, force: bool = False) -> SubstackSyncResult:
        """Synchronise tous les articles (posts et newsletters) depuis les sous-répertoires.

        Args:
            force: Si True, écrase les fichiers existants.

        Returns:
            SubstackSyncResult avec le détail des fichiers traités.

        Raises:
            FileNotFoundError: Si le répertoire source n'existe pas.
        """
        if not self.source_dir.exists():
            raise FileNotFoundError(
                f"Répertoire source introuvable : {self.source_dir}\n"
                "Vérifiez SUBSTACK_EXTRACT_OUTPUT dans .env"
            )

        result = SubstackSyncResult()

        # Chercher tous les fichiers markdown récursivement
        all_files = list(self.source_dir.rglob("*.md"))
        logger.info(f"SubstackBridge: {len(all_files)} fichiers markdown trouvés")

        # Créer les répertoires de destination
        dest_posts = self.dest_dir / "posts"
        dest_newsletters = self.dest_dir / "newsletters"
        dest_posts.mkdir(parents=True, exist_ok=True)
        dest_newsletters.mkdir(parents=True, exist_ok=True)

        for source_file in all_files:
            # Déterminer le type de contenu et la destination
            is_newsletter = self._is_newsletter(source_file)
            content_type = "newsletter" if is_newsletter else "post"
            dest_dir = dest_newsletters if is_newsletter else dest_posts
            dest_file = dest_dir / source_file.name

            if dest_file.exists() and not force:
                logger.debug(f"Ignoré (déjà présent) : {source_file.name}")
                if is_newsletter:
                    result.newsletters_skipped.append(source_file)
                else:
                    result.posts_skipped.append(source_file)
                continue

            try:
                self._copy_with_metadata(source_file, dest_file, content_type=content_type)
                logger.info(f"Copié ({content_type}): {source_file.name}")
                if is_newsletter:
                    result.newsletters_synced.append(source_file)
                else:
                    result.posts_synced.append(source_file)
            except Exception as e:
                logger.error(f"Erreur {source_file.name}: {e}")
                result.errors.append((source_file, str(e)))

        logger.info(result.summary())
        return result

    def _copy_with_metadata(
        self,
        source: Path,
        dest: Path,
        content_type: str = "post",
    ) -> None:
        """Copie un fichier en préservant et enrichissant les métadonnées.

        Args:
            source: Fichier source.
            dest: Fichier destination.
            content_type: Type de contenu ("post" ou "newsletter").

        Raises:
            OSError: Si la copie échoue.
        """
        try:
            post = frontmatter.load(str(source))
            if "source" not in post.metadata:
                post.metadata["source"] = "substack"
            if "content_type" not in post.metadata:
                post.metadata["content_type"] = content_type
            if "raw_path" not in post.metadata:
                post.metadata["raw_path"] = str(source)
            dest.write_text(frontmatter.dumps(post), encoding="utf-8")
        except Exception:
            logger.warning(f"Frontmatter invalide pour {source.name}, copie brute")
            shutil.copy2(source, dest)
