"""Gestion des backlinks bidirectionnels entre articles et fiches wiki.

Le Linker maintient la cohérence des liens [[wikilink]] dans le vault :
- Concept → Articles : section "## Sources" des fiches (géré par ConceptManager)
- Article → Concepts : section "## Concepts extraits" ajoutée à l'article RAW
- Concept → Concept : section "## Concepts liés" des fiches
"""

import logging
import re
from pathlib import Path

from src.config import get_settings

logger = logging.getLogger(__name__)


class Linker:
    """Gère les backlinks bidirectionnels dans le vault Obsidian.

    Attributes:
        vault_path: Chemin racine du vault Obsidian.
        wiki_root: Chemin vers 02_WIKI/.
        raw_root: Chemin vers 00_RAW/.
    """

    def __init__(self) -> None:
        """Initialise avec la configuration courante."""
        settings = get_settings()
        self.vault_path = Path(settings.vault_path)
        self.wiki_root = self.vault_path / "02_WIKI"
        self.raw_root = self.vault_path / "00_RAW"

    def add_concepts_to_article(
        self,
        article_path: Path,
        concept_names: list[str],
    ) -> int:
        """Ajoute ou met à jour la section "Concepts extraits" dans un article RAW.

        Injecte les liens [[concept]] dans une section dédiée à la fin du fichier,
        sans modifier le contenu principal de l'article.

        Args:
            article_path: Chemin du fichier article (dans 00_RAW/).
            concept_names: Noms des concepts extraits à lier.

        Returns:
            Nombre de nouveaux liens ajoutés.

        Raises:
            OSError: Si le fichier ne peut pas être lu ou écrit.
        """
        if not concept_names:
            return 0

        content = article_path.read_text(encoding="utf-8")
        section_header = "## Concepts extraits"

        # Construire les liens wikilinks
        new_links = set(concept_names)

        # Vérifier les liens déjà présents dans la section
        existing_section_match = re.search(
            r"## Concepts extraits\n(.*?)(\n## |\Z)", content, re.DOTALL
        )

        if existing_section_match:
            existing_content = existing_section_match.group(1)
            existing_links = set(re.findall(r"\[\[([^\]]+)\]\]", existing_content))
            links_to_add = new_links - existing_links

            if not links_to_add:
                return 0

            # Ajouter les nouveaux liens à la section existante
            additional = "\n".join(f"- [[{name}]]" for name in sorted(links_to_add))
            insert_pos = existing_section_match.end(1)
            content = content[:insert_pos].rstrip() + "\n" + additional + content[insert_pos:]
            added_count = len(links_to_add)
        else:
            # Créer la section à la fin du fichier
            links_block = "\n".join(f"- [[{name}]]" for name in sorted(new_links))
            section = f"\n\n{section_header}\n\n{links_block}\n"

            # Supprimer l'ancienne section wiki_compiled si présente (migration)
            content = content.rstrip() + section
            added_count = len(new_links)

        article_path.write_text(content, encoding="utf-8")
        logger.debug(f"Article {article_path.name} : {added_count} liens ajoutés")
        return added_count

    def add_related_concepts(
        self,
        concept_path: Path,
        related_names: list[str],
    ) -> int:
        """Ajoute des liens vers des concepts liés dans une fiche wiki.

        Met à jour la section "## Concepts liés" de la fiche.

        Args:
            concept_path: Chemin de la fiche concept à mettre à jour.
            related_names: Noms des concepts à lier.

        Returns:
            Nombre de nouveaux liens ajoutés.
        """
        if not related_names:
            return 0

        content = concept_path.read_text(encoding="utf-8")
        new_links = set(related_names)

        # Vérifier les liens déjà présents dans "Concepts liés"
        section_match = re.search(r"## Concepts liés\n(.*?)(\n## |\Z)", content, re.DOTALL)

        if section_match:
            existing_content = section_match.group(1)
            existing_links = set(re.findall(r"\[\[([^\]]+)\]\]", existing_content))
            # Ne pas s'auto-lier
            concept_title = concept_path.stem
            links_to_add = new_links - existing_links - {concept_title}

            if not links_to_add:
                return 0

            additional = "\n".join(f"- [[{name}]]" for name in sorted(links_to_add))
            insert_pos = section_match.end(1)
            # Remplacer le placeholder si présent
            updated_content = content[: section_match.start(1)]
            existing_stripped = existing_content.strip()
            if existing_stripped and existing_stripped != "_À compléter_":
                updated_content += existing_content.rstrip() + "\n" + additional
            else:
                updated_content += "\n" + additional
            updated_content += content[insert_pos:]
            content = updated_content
            added_count = len(links_to_add)
        else:
            # Section absente : l'ajouter
            links_block = "\n".join(f"- [[{name}]]" for name in sorted(new_links))
            content = content.rstrip() + f"\n\n## Concepts liés\n\n{links_block}\n"
            added_count = len(new_links)

        concept_path.write_text(content, encoding="utf-8")
        return added_count

    def get_backlinks(self, article_stem: str) -> list[Path]:
        """Retourne les fiches wiki qui référencent un article donné.

        Args:
            article_stem: Nom du fichier article sans extension.

        Returns:
            Liste des chemins de fiches wiki contenant [[article_stem]].
        """
        if not self.wiki_root.exists():
            return []

        backlinks: list[Path] = []
        search_pattern = f"[[{article_stem}]]"

        for wiki_file in self.wiki_root.rglob("*.md"):
            try:
                content = wiki_file.read_text(encoding="utf-8")
                if search_pattern in content:
                    backlinks.append(wiki_file)
            except OSError as e:
                logger.warning(f"Impossible de lire {wiki_file}: {e}")

        return backlinks

    def get_article_concepts(self, article_path: Path) -> list[str]:
        """Retourne les concepts liés à un article (depuis sa section Concepts extraits).

        Args:
            article_path: Chemin de l'article RAW.

        Returns:
            Liste des noms de concepts extraits de l'article.
        """
        try:
            content = article_path.read_text(encoding="utf-8")
        except OSError:
            return []

        section_match = re.search(r"## Concepts extraits\n(.*?)(\n## |\Z)", content, re.DOTALL)
        if not section_match:
            return []

        return re.findall(r"\[\[([^\]]+)\]\]", section_match.group(1))
