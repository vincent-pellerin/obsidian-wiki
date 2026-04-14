"""Moteur Q&A — interroge le wiki en langage naturel.

Pipeline :
  1. Recherche via qmd (BM25 full-text dans 02_WIKI/)
  2. Chargement et agrégation du contenu des fiches pertinentes
  3. Construction du prompt avec contexte
  4. Appel Gemini pour synthétiser la réponse
  5. Retour d'un QueryResult sourcé
"""

import json
import logging
import subprocess
import time
from pathlib import Path

from src.config import get_settings
from src.qa.models import QueryResult

logger = logging.getLogger(__name__)

# Nombre max de tentatives pour l'appel Gemini
MAX_RETRIES = 3
RETRY_DELAY_S = 5.0

# Longueur maximale du contenu d'une fiche (en caractères)
MAX_FICHE_CHARS = 2000

QA_PROMPT = """\
Tu es un assistant expert basé sur une knowledge base personnelle.
Réponds à la question en utilisant UNIQUEMENT les informations du contexte fourni.
Si l'information n'est pas dans le contexte, dis-le clairement.
Cite les sources entre [[doubles crochets]] quand tu utilises leur contenu.

Contexte (fiches wiki) :
{context}

Question : {question}

Réponse (en français, structurée avec des sections si nécessaire) :
"""


class QAEngine:
    """Moteur de questions/réponses basé sur le wiki Obsidian.

    Effectue une recherche full-text dans les fiches wiki, agrège le contexte
    pertinent et appelle Gemini pour synthétiser une réponse sourcée.

    Attributes:
        vault_path: Chemin racine du vault Obsidian.
        wiki_path: Chemin du répertoire 02_WIKI/.
        model_name: Nom du modèle Gemini utilisé.
    """

    def __init__(self, model_override: str | None = None) -> None:
        """Initialise le moteur Q&A avec la configuration courante.

        Args:
            model_override: Nom de modèle Gemini à utiliser à la place de
                celui défini dans la configuration.
        """
        settings = get_settings()
        self.vault_path = Path(settings.get_vault_path())
        self.wiki_path = self.vault_path / "02_WIKI"
        self._settings = settings
        self.model_name = model_override or settings.gemini_model_wiki
        if model_override:
            logger.info(
                f"Modèle override : {model_override} (config : {settings.gemini_model_wiki})"
            )

    def query(self, question: str, max_sources: int = 10) -> QueryResult:
        """Répond à une question en s'appuyant sur le contenu du wiki.

        Args:
            question: La question posée en langage naturel.
            max_sources: Nombre maximum de fiches wiki à utiliser comme contexte.

        Returns:
            QueryResult avec la réponse, les sources et les statistiques de tokens.
        """
        logger.info(f"Q&A query : {question[:100]!r}")

        # Recherche des fiches pertinentes
        wiki_files = self._search_wiki(question, max_results=max_sources)
        logger.info(f"Fiches trouvées : {len(wiki_files)}")

        if not wiki_files:
            return QueryResult(
                question=question,
                answer=(
                    "Aucune fiche wiki pertinente trouvée pour cette question. "
                    "Le wiki est peut-être vide ou la question ne correspond "
                    "à aucun contenu indexé."
                ),
                sources=[],
                concepts_used=[],
            )

        # Construction du contexte
        context, sources = self._build_context(wiki_files)
        concepts_used = [f.stem for f in wiki_files]

        # Appel Gemini
        try:
            answer, input_tokens, output_tokens = self._call_gemini(question, context)
        except RuntimeError as e:
            logger.error(f"Erreur Gemini Q&A : {e}")
            return QueryResult(
                question=question,
                answer=f"Erreur lors de la génération de la réponse : {e}",
                sources=sources,
                concepts_used=concepts_used,
            )

        return QueryResult(
            question=question,
            answer=answer,
            sources=sources,
            concepts_used=concepts_used,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    def _search_wiki(self, question: str, max_results: int) -> list[Path]:
        """Recherche dans le wiki via qmd (BM25 full-text).

        Utilise qmd search en subprocess pour obtenir les fichiers les plus
        pertinents selon la question posée.

        Args:
            question: La question à rechercher.
            max_results: Nombre maximum de résultats à retourner.

        Returns:
            Liste des chemins de fiches triés par pertinence décroissante.
        """
        if not self.wiki_path.exists():
            logger.warning(f"Répertoire wiki introuvable : {self.wiki_path}")
            return []

        try:
            # Appel qmd search via subprocess
            result = subprocess.run(
                [
                    "qmd",
                    "search",
                    question,
                    "-c",
                    "wiki",
                    "-n",
                    str(max_results),
                    "--json",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )

            if result.returncode != 0:
                logger.warning(f"qmd search failed: {result.stderr}")
                return []

            # Parse JSON results
            hits = json.loads(result.stdout)
            paths: list[Path] = []

            for hit in hits:
                # qmd retourne les chemins au format qmd://wiki/path/to/file.md
                # On convertit en chemin filesystem
                qmd_path = hit.get("path", "")
                if qmd_path.startswith("qmd://wiki/"):
                    rel_path = qmd_path.replace("qmd://wiki/", "")
                    full_path = self.wiki_path / rel_path
                    if full_path.exists():
                        paths.append(full_path)

            logger.info(f"qmd search: {len(paths)} résultats pour {question[:50]!r}")
            return paths

        except subprocess.TimeoutExpired:
            logger.warning("qmd search timeout")
            return []
        except json.JSONDecodeError as e:
            logger.warning(f"qmd search JSON parse error: {e}")
            return []
        except Exception as e:
            logger.warning(f"qmd search error: {e}")
            return []

    def _build_context(self, wiki_files: list[Path]) -> tuple[str, list[str]]:
        """Lit et agrège le contenu des fiches wiki.

        Chaque fiche est tronquée à MAX_FICHE_CHARS caractères pour éviter
        de dépasser la fenêtre de contexte du LLM.

        Args:
            wiki_files: Liste des chemins de fiches à inclure.

        Returns:
            Tuple (context_string, list_of_stems) où context_string est le
            contenu agrégé et list_of_stems les identifiants des sources.
        """
        parts: list[str] = []
        stems: list[str] = []

        for wiki_file in wiki_files:
            try:
                content = wiki_file.read_text(encoding="utf-8")
                # Tronquer si nécessaire
                if len(content) > MAX_FICHE_CHARS:
                    content = content[:MAX_FICHE_CHARS] + "\n[... tronqué ...]"
                parts.append(f"### [[{wiki_file.stem}]]\n{content}")
                stems.append(wiki_file.stem)
            except OSError as e:
                logger.warning(f"Impossible de lire {wiki_file.name} : {e}")

        context = "\n\n---\n\n".join(parts)
        return context, stems

    def _call_gemini(self, question: str, context: str) -> tuple[str, int, int]:
        """Appelle Gemini avec retry pour générer une réponse.

        Args:
            question: La question posée.
            context: Le contexte agrégé des fiches wiki.

        Returns:
            Tuple (answer, input_tokens, output_tokens).

        Raises:
            RuntimeError: Si toutes les tentatives ont échoué.
        """
        try:
            from google import genai
        except ImportError as e:
            raise RuntimeError("google-genai non installé. Lancez : uv sync") from e

        api_key = self._settings.get_gemini_api_key()
        if not api_key:
            raise RuntimeError("Clé API Gemini non configurée (GEMINI_API_KEY ou GOOGLE_API_KEY)")

        client = genai.Client(api_key=api_key)
        prompt = QA_PROMPT.format(context=context, question=question)

        last_error: Exception | None = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = client.models.generate_content(model=self.model_name, contents=prompt)
                input_tokens = 0
                output_tokens = 0
                if response.usage_metadata:
                    input_tokens = response.usage_metadata.prompt_token_count or 0
                    output_tokens = response.usage_metadata.candidates_token_count or 0
                return response.text, input_tokens, output_tokens
            except Exception as e:
                last_error = e
                if attempt < MAX_RETRIES:
                    logger.warning(
                        f"Gemini tentative {attempt}/{MAX_RETRIES} échouée : {e}. "
                        f"Retry dans {RETRY_DELAY_S}s..."
                    )
                    time.sleep(RETRY_DELAY_S)
                else:
                    logger.error(f"Gemini : {MAX_RETRIES} tentatives épuisées.")

        raise RuntimeError(f"Appel Gemini échoué après {MAX_RETRIES} tentatives") from last_error
