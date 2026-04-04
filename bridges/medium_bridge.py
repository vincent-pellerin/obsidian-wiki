"""Bridge Medium → vault Obsidian.

Copie les articles extraits depuis medium_extract/output/
vers 00_RAW/articles/medium/ du vault, avec déduplication.
"""

import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import frontmatter

from src.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class SyncResult:
    """Résultat d'une synchronisation bridge.

    Attributes:
        synced: Articles copiés avec succès.
        skipped: Articles déjà présents (dédupliqués).
        errors: Articles ayant échoué avec leur message d'erreur.
    """

    synced: list[Path] = field(default_factory=list)
    skipped: list[Path] = field(default_factory=list)
    errors: list[tuple[Path, str]] = field(default_factory=list)

    @property
    def total(self) -> int:
        """Nombre total d'articles traités."""
        return len(self.synced) + len(self.skipped) + len(self.errors)

    def summary(self) -> str:
        """Résumé lisible du résultat."""
        return (
            f"MediumBridge sync: {len(self.synced)} copiés, "
            f"{len(self.skipped)} ignorés, {len(self.errors)} erreurs "
            f"(total: {self.total})"
        )


class MediumBridge:
    """Pont entre medium_extract et le vault Obsidian.

    Synchronise les articles markdown depuis le répertoire de sortie
    de medium_extract vers 00_RAW/articles/medium/ du vault.

    Attributes:
        source_dir: Répertoire source (medium_extract/output/).
        dest_dir: Répertoire destination (vault/00_RAW/articles/medium/).
    """

    def __init__(self) -> None:
        """Initialise le bridge avec la configuration courante."""
        settings = get_settings()
        self.source_dir = Path(settings.medium_extract_output)
        self.dest_dir = Path(settings.get_vault_path()) / "00_RAW" / "articles" / "medium"

    def sync_to_raw(self, *, force: bool = False) -> SyncResult:
        """Copie les articles vers 00_RAW/articles/medium/.

        Préserve les métadonnées YAML frontmatter.
        Déduplication par nom de fichier (= article_id).
        Skip si déjà présent (sauf si force=True).

        Args:
            force: Si True, écrase les fichiers existants.

        Returns:
            SyncResult avec le détail des articles traités.

        Raises:
            FileNotFoundError: Si le répertoire source n'existe pas.
        """
        if not self.source_dir.exists():
            raise FileNotFoundError(
                f"Répertoire source introuvable : {self.source_dir}\n"
                "Vérifiez MEDIUM_EXTRACT_OUTPUT dans .env"
            )

        self.dest_dir.mkdir(parents=True, exist_ok=True)
        result = SyncResult()

        markdown_files = list(self.source_dir.glob("*.md"))
        logger.info(f"MediumBridge: {len(markdown_files)} fichiers trouvés dans {self.source_dir}")

        for source_file in markdown_files:
            dest_file = self.dest_dir / source_file.name

            # Déduplication : skip si déjà présent
            if dest_file.exists() and not force:
                logger.debug(f"Ignoré (déjà présent) : {source_file.name}")
                result.skipped.append(source_file)
                continue

            try:
                self._copy_with_metadata(source_file, dest_file)
                logger.info(f"Copié : {source_file.name}")
                result.synced.append(source_file)
            except Exception as e:
                logger.error(f"Erreur lors de la copie de {source_file.name}: {e}")
                result.errors.append((source_file, str(e)))

        logger.info(result.summary())
        return result

    def get_pending_articles(self) -> list[Path]:
        """Retourne les articles non encore compilés dans le wiki.

        Un article est "pending" s'il est dans 00_RAW/articles/medium/
        mais qu'aucune fiche concept n'y fait référence dans 02_WIKI/.

        Returns:
            Liste des chemins d'articles en attente de compilation.
        """
        settings = get_settings()
        wiki_dir = Path(settings.get_vault_path()) / "02_WIKI"

        if not self.dest_dir.exists():
            return []

        raw_articles = set(self.dest_dir.glob("*.md"))
        if not wiki_dir.exists():
            return list(raw_articles)

        # Collecte toutes les références [[...]] dans le wiki
        referenced = set()
        for wiki_file in wiki_dir.rglob("*.md"):
            try:
                content = wiki_file.read_text(encoding="utf-8")
                # Extraire les backlinks [[article_id]]
                import re

                links = re.findall(r"\[\[([^\]]+)\]\]", content)
                referenced.update(links)
            except OSError as e:
                logger.warning(f"Impossible de lire {wiki_file}: {e}")

        pending = [article for article in raw_articles if article.stem not in referenced]

        logger.info(f"Articles pending : {len(pending)}/{len(raw_articles)}")
        return pending

    def _copy_with_metadata(self, source: Path, dest: Path) -> None:
        """Copie un fichier en préservant les métadonnées YAML frontmatter.

        Args:
            source: Fichier source.
            dest: Fichier destination.

        Raises:
            ValueError: Si le fichier source n'est pas un markdown valide.
            OSError: Si la copie échoue.
        """
        try:
            # Valider que le frontmatter est lisible
            post = frontmatter.load(str(source))
            # Ajouter métadonnée source si absente
            if "source" not in post.metadata:
                post.metadata["source"] = "medium"
            if "raw_path" not in post.metadata:
                post.metadata["raw_path"] = str(source)
            dest.write_text(frontmatter.dumps(post), encoding="utf-8")
        except Exception:
            # Fallback : copie brute si frontmatter invalide
            logger.warning(f"Frontmatter invalide pour {source.name}, copie brute")
            shutil.copy2(source, dest)
