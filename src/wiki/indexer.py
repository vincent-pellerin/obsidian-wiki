"""Génération et mise à jour de l'index maître du wiki.

Maintient 02_WIKI/Index/000_Master_Index.md avec :
- Statistiques globales
- Index par catégorie (Concepts, People, Technologies, Topics)
- Articles récemment compilés
- Fiches les plus connectées (source_count élevé)
"""

import logging
from datetime import datetime
from pathlib import Path

import frontmatter

from src.config import get_settings
from src.wiki.concept_manager import WIKI_TYPE_DIRS

logger = logging.getLogger(__name__)

INDEX_FILE = "000_Master_Index.md"


class Indexer:
    """Génère et maintient l'index maître du vault wiki.

    Attributes:
        wiki_root: Chemin vers 02_WIKI/.
        index_dir: Chemin vers 02_WIKI/Index/.
        index_path: Chemin vers 000_Master_Index.md.
    """

    def __init__(self) -> None:
        """Initialise avec la configuration courante."""
        settings = get_settings()
        self.wiki_root = Path(settings.vault_path) / "02_WIKI"
        self.index_dir = self.wiki_root / "Index"
        self.index_path = self.index_dir / INDEX_FILE

    def build_master_index(self) -> Path:
        """Génère ou régénère l'index maître complet.

        Scanne tous les fichiers de 02_WIKI/ et construit une vue
        structurée par catégorie avec statistiques.

        Returns:
            Chemin vers le fichier index généré.
        """
        self.index_dir.mkdir(parents=True, exist_ok=True)

        stats = self._collect_stats()
        content = self._render_index(stats)

        self.index_path.write_text(content, encoding="utf-8")
        logger.info(
            f"Index maître généré : {stats['total_fiches']} fiches, "
            f"{stats['total_sources']} sources"
        )
        return self.index_path

    def _collect_stats(self) -> dict:
        """Collecte les statistiques du wiki pour l'index.

        Returns:
            Dictionnaire avec :
            - total_fiches: int
            - total_sources: int
            - by_type: dict[str, list[dict]] (par type de fiche)
            - top_connected: list[dict] (fiches avec + de sources)
        """
        stats: dict = {
            "total_fiches": 0,
            "total_sources": 0,
            "by_type": {},
            "top_connected": [],
        }

        all_entries: list[dict] = []

        for wiki_type, subdir in WIKI_TYPE_DIRS.items():
            type_dir = self.wiki_root / subdir
            if not type_dir.exists():
                stats["by_type"][wiki_type] = []
                continue

            entries: list[dict] = []
            for md_file in sorted(type_dir.glob("*.md")):
                entry = self._read_entry_meta(md_file)
                if entry:
                    entries.append(entry)
                    all_entries.append(entry)
                    stats["total_fiches"] += 1
                    stats["total_sources"] += entry.get("source_count", 0)

            stats["by_type"][wiki_type] = entries

        # Top 10 des fiches les plus connectées
        stats["top_connected"] = sorted(
            all_entries,
            key=lambda e: e.get("source_count", 0),
            reverse=True,
        )[:10]

        return stats

    def _read_entry_meta(self, md_file: Path) -> dict | None:
        """Lit les métadonnées d'une fiche wiki.

        Args:
            md_file: Chemin du fichier markdown.

        Returns:
            Dict avec title, type, source_count, updated, ou None si erreur.
        """
        try:
            post = frontmatter.load(str(md_file))
            return {
                "title": post.metadata.get("title", md_file.stem),
                "type": post.metadata.get("type", "unknown"),
                "source_count": int(post.metadata.get("source_count", 0)),
                "updated": str(post.metadata.get("updated", "")),
                "category": post.metadata.get("category", ""),
                "stem": md_file.stem,
                "path": md_file,
            }
        except Exception as e:
            logger.warning(f"Impossible de lire les métadonnées de {md_file.name}: {e}")
            return None

    def _render_index(self, stats: dict) -> str:
        """Génère le contenu Markdown de l'index maître.

        Args:
            stats: Statistiques collectées par _collect_stats().

        Returns:
            Contenu Markdown de l'index.
        """
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        lines: list[str] = []

        # En-tête
        lines += [
            "---",
            "title: Index Maître du Wiki",
            "type: index",
            f"updated: {now}",
            "---",
            "",
            "# Index Maître du Wiki",
            "",
            f"> Généré automatiquement le {now}",
            "",
            "---",
            "",
            "## Statistiques",
            "",
            f"- **Total fiches** : {stats['total_fiches']}",
            f"- **Total sources indexées** : {stats['total_sources']}",
        ]

        # Stats par type
        for wiki_type, entries in stats["by_type"].items():
            label = WIKI_TYPE_DIRS.get(wiki_type, wiki_type)
            lines.append(f"- **{label}** : {len(entries)} fiches")

        lines += ["", "---", ""]

        # Index par catégorie
        type_labels = {
            "concept": "Concepts",
            "person": "Personnes",
            "technology": "Technologies",
            "topic": "Topics",
        }

        for wiki_type, entries in stats["by_type"].items():
            label = type_labels.get(wiki_type, wiki_type.capitalize())
            lines += [f"## {label}", ""]

            if not entries:
                lines += ["_Aucune fiche pour l'instant._", ""]
                continue

            for entry in sorted(entries, key=lambda e: e["title"].lower()):
                count = entry["source_count"]
                suffix = f" _(×{count})_" if count > 1 else ""
                lines.append(f"- [[{entry['stem']}]]{suffix}")

            lines.append("")

        # Top fiches connectées
        if stats["top_connected"]:
            lines += ["---", "", "## Fiches les plus connectées", ""]
            for i, entry in enumerate(stats["top_connected"], 1):
                lines.append(f"{i}. [[{entry['stem']}]] — {entry['source_count']} source(s)")
            lines.append("")

        return "\n".join(lines)
