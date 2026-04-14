"""Générateur de rapports Markdown structurés.

Génère un rapport complet sur un topic en interrogeant le QAEngine
et en formatant le résultat avec un frontmatter YAML.
"""

import logging
import re
from datetime import date
from pathlib import Path

from src.config import get_settings
from src.qa.engine import QAEngine
from src.qa.models import ReportResult

logger = logging.getLogger(__name__)


def _slugify(text: str) -> str:
    """Convertit un texte en slug URL-safe.

    Args:
        text: Texte à convertir.

    Returns:
        Slug en minuscules avec tirets.
    """
    slug = text.lower()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    return slug.strip("-")[:80]


class ReportGenerator:
    """Génère des rapports Markdown structurés sur un topic.

    Utilise QAEngine pour interroger le wiki et formate le résultat
    avec un frontmatter YAML et un en-tête standardisé.

    Attributes:
        qa_engine: Instance du moteur Q&A.
        vault_path: Chemin racine du vault Obsidian.
    """

    def __init__(self) -> None:
        """Initialise le générateur avec la configuration courante."""
        settings = get_settings()
        self.qa_engine = QAEngine()
        self.vault_path = Path(settings.get_vault_path())

    def generate(self, topic: str, *, output_dir: Path | None = None) -> ReportResult:
        """Génère un rapport Markdown sur le topic.

        Pipeline :
        1. Query QAEngine avec "Fais un rapport complet sur : {topic}"
        2. Ajoute un frontmatter YAML (title, date, topic, sources)
        3. Sauvegarde dans 03_OUTPUT/Reports/{date}_{slug}.md

        Args:
            topic: Sujet du rapport à générer.
            output_dir: Répertoire de sortie (défaut : 03_OUTPUT/Reports/).

        Returns:
            ReportResult avec le chemin du fichier généré et les statistiques.
        """
        logger.info(f"Génération rapport : {topic!r}")

        # Interroger le QAEngine
        query_result = self.qa_engine.query(
            f"Fais un rapport complet sur : {topic}",
            max_sources=10,
        )

        # Déterminer le répertoire de sortie
        if output_dir is None:
            output_dir = self.vault_path / "03_OUTPUT" / "Reports"
        output_dir.mkdir(parents=True, exist_ok=True)

        # Construire le nom de fichier
        today = date.today().isoformat()
        slug = _slugify(topic)
        filename = f"{today}_{slug}.md"
        output_path = output_dir / filename

        # Construire le contenu du rapport
        sources_yaml = "\n".join(f"  - [[{src}]]" for src in query_result.sources)
        if not sources_yaml:
            sources_yaml = "  []"

        content = (
            f"---\n"
            f'title: "Rapport : {topic}"\n'
            f"date: {today}\n"
            f'topic: "{topic}"\n'
            f"sources:\n{sources_yaml}\n"
            f"---\n\n"
            f"# Rapport : {topic}\n\n"
            f"> Généré automatiquement le {today}\n\n"
            f"{query_result.answer}\n"
        )

        # Sauvegarder
        output_path.write_text(content, encoding="utf-8")
        logger.info(f"Rapport sauvegardé : {output_path}")

        # Calculer les statistiques
        word_count = len(query_result.answer.split())

        return ReportResult(
            topic=topic,
            output_path=output_path,
            word_count=word_count,
            sources_count=len(query_result.sources),
        )
