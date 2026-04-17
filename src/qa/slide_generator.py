"""Générateur de slides Marp.

Génère une présentation Marp structurée sur un topic en interrogeant
le QAEngine puis en appelant Gemini pour formater en slides.
"""

import logging
import re
import time
from datetime import date
from pathlib import Path

from src.config import get_settings
from src.qa.engine import QAEngine
from src.qa.models import SlideResult

logger = logging.getLogger(__name__)

# Nombre max de tentatives pour l'appel Gemini
MAX_RETRIES = 3
RETRY_DELAY_S = 5.0

SLIDE_PROMPT = """\
Tu es un expert en présentation. Génère des slides Marp sur le sujet demandé.
Utilise UNIQUEMENT les informations du contexte fourni.
Format Marp : chaque slide séparée par "---", titre avec "# ", sous-titres avec "## ".
Maximum 10 slides. Commence par un slide titre et termine par un slide "Questions ?".
Contenu concis, bullet points, pas de longs paragraphes.

Contexte :
{context}

Sujet de la présentation : {topic}

Slides Marp :
"""

MARP_HEADER = """\
---
marp: true
theme: default
paginate: true
---

"""


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


def _count_slides(content: str) -> int:
    """Compte le nombre de slides dans le contenu Marp.

    Args:
        content: Contenu Marp brut.

    Returns:
        Nombre de slides (séparateurs --- + 1).
    """
    # Compter les séparateurs --- qui ne font pas partie du frontmatter
    # On cherche les --- en début de ligne entourés de sauts de ligne
    separators = re.findall(r"(?m)^---$", content)
    return max(1, len(separators) + 1)


class SlideGenerator:
    """Génère des présentations Marp sur un topic.

    Utilise QAEngine pour récupérer le contexte, puis appelle Gemini
    avec un prompt spécialisé pour structurer le contenu en slides.

    Attributes:
        qa_engine: Instance du moteur Q&A.
        vault_path: Chemin racine du vault Obsidian.
        model_name: Nom du modèle Gemini utilisé.
    """

    def __init__(self) -> None:
        """Initialise le générateur avec la configuration courante."""
        settings = get_settings()
        self.qa_engine = QAEngine()
        self.vault_path = Path(settings.get_vault_path())
        self._settings = settings
        self.model_name = settings.gemini_model_wiki

    def generate(self, topic: str, *, output_dir: Path | None = None) -> SlideResult:
        """Génère des slides Marp sur le topic.

        Pipeline :
        1. Query QAEngine avec "Génère une présentation sur : {topic}"
        2. Appelle Gemini avec SLIDE_PROMPT pour structurer en slides
        3. Ajoute l'en-tête Marp au début
        4. Sauvegarde dans 03_OUTPUT/Slides/{date}_{slug}.md

        Args:
            topic: Sujet de la présentation.
            output_dir: Répertoire de sortie (défaut : 03_OUTPUT/Slides/).

        Returns:
            SlideResult avec le chemin du fichier généré et le nombre de slides.
        """
        logger.info(f"Génération slides : {topic!r}")

        # Récupérer le contexte via QAEngine
        query_result = self.qa_engine.query(
            f"Génère une présentation sur : {topic}",
            max_sources=10,
        )

        # Construire le contexte pour le prompt slides
        context = query_result.answer
        if query_result.sources:
            sources_list = ", ".join(f"[[{s}]]" for s in query_result.sources)
            context += f"\n\nSources : {sources_list}"

        # Appeler Gemini pour structurer en slides
        try:
            slides_content = self._call_gemini_slides(topic, context)
        except RuntimeError as e:
            logger.error(f"Erreur Gemini slides : {e}")
            # Fallback : créer une slide minimale
            slides_content = (
                f"# {topic}\n\nErreur lors de la génération : {e}\n\n---\n\n# Questions ?\n"
            )

        # Ajouter l'en-tête Marp
        full_content = MARP_HEADER + slides_content

        # Déterminer le répertoire de sortie
        if output_dir is None:
            output_dir = self.vault_path / "03_OUTPUT" / "Slides"
        output_dir.mkdir(parents=True, exist_ok=True)

        # Construire le nom de fichier
        today = date.today().isoformat()
        slug = _slugify(topic)
        filename = f"{today}_{slug}.md"
        output_path = output_dir / filename

        # Sauvegarder
        output_path.write_text(full_content, encoding="utf-8")
        logger.info(f"Slides sauvegardées : {output_path}")

        slides_count = _count_slides(slides_content)

        return SlideResult(
            topic=topic,
            output_path=output_path,
            slides_count=slides_count,
        )

    def _call_gemini_slides(self, topic: str, context: str) -> str:
        """Appelle Gemini pour structurer le contenu en slides Marp.

        Args:
            topic: Sujet de la présentation.
            context: Contexte agrégé depuis le wiki.

        Returns:
            Contenu Marp brut (sans l'en-tête).

        Raises:
            RuntimeError: Si toutes les tentatives ont échoué.
        """
        try:
            from google import genai
        except ImportError as e:
            raise RuntimeError("google-genai non installé. Lancez : uv sync") from e

        api_key = self._settings.get_gemini_api_key()
        if not api_key:
            raise RuntimeError("Clé API Gemini non configurée (GEMINI_API_KEY ou GEMINI_API_KEY_2)")

        client = genai.Client(api_key=api_key)
        prompt = SLIDE_PROMPT.format(context=context, topic=topic)

        last_error: Exception | None = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = client.models.generate_content(model=self.model_name, contents=prompt)
                return response.text
            except Exception as e:
                last_error = e
                if attempt < MAX_RETRIES:
                    logger.warning(
                        f"Gemini slides tentative {attempt}/{MAX_RETRIES} échouée : {e}. "
                        f"Retry dans {RETRY_DELAY_S}s..."
                    )
                    time.sleep(RETRY_DELAY_S)
                else:
                    logger.error(f"Gemini slides : {MAX_RETRIES} tentatives épuisées.")

        raise RuntimeError(
            f"Appel Gemini slides échoué après {MAX_RETRIES} tentatives"
        ) from last_error
