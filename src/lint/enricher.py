"""Enricher — suggère et applique des améliorations au wiki.

Détecte les connexions manquantes entre concepts et enrichit les fiches
via Gemini en s'appuyant sur les sources disponibles.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import frontmatter

from src.config import get_settings
from src.wiki.concept_manager import ConceptManager

logger = logging.getLogger(__name__)

# Nombre max de tentatives pour l'appel Gemini
MAX_RETRIES = 3
RETRY_DELAY_S = 5.0

# Longueur maximale du contenu source envoyé au LLM
MAX_SOURCE_CHARS = 3000

# Prix gemini-2.5-flash-lite ($/M tokens)
_PRICE_INPUT = 0.10
_PRICE_OUTPUT = 0.40


@dataclass
class EnrichResult:
    """Résultat d'enrichissement d'une fiche."""

    concept_name: str
    success: bool = False
    input_tokens: int = 0
    output_tokens: int = 0
    error: str = ""


@dataclass
class EnrichBatchResult:
    """Résultat agrégé d'un batch d'enrichissement."""

    results: list[EnrichResult] = field(default_factory=list)

    @property
    def total_enriched(self) -> int:
        return sum(1 for r in self.results if r.success)

    @property
    def total_errors(self) -> int:
        return sum(1 for r in self.results if not r.success)

    @property
    def total_input_tokens(self) -> int:
        return sum(r.input_tokens for r in self.results)

    @property
    def total_output_tokens(self) -> int:
        return sum(r.output_tokens for r in self.results)

    @property
    def total_cost(self) -> float:
        return (
            self.total_input_tokens / 1_000_000 * _PRICE_INPUT
            + self.total_output_tokens / 1_000_000 * _PRICE_OUTPUT
        )

ENRICH_PROMPT = """\
Tu es un expert en knowledge management. Enrichis la fiche wiki suivante.

Fiche actuelle :
{current_content}

Contenu des sources (articles) :
{sources_content}

Tâche :
1. Améliore la section "Définition" ou "Description" avec des informations précises des sources
2. Ajoute des exemples concrets si disponibles dans les sources
3. Suggère des connexions avec d'autres concepts (section "Concepts liés")
4. Identifie des questions ouvertes pertinentes (section "Questions ouvertes")

Retourne UNIQUEMENT le contenu markdown enrichi (sans frontmatter, sans balises de code).
Conserve la structure existante et améliore le contenu.
"""


class Enricher:
    """Suggère et applique des améliorations au wiki.

    Analyse les connexions entre concepts et enrichit les fiches
    avec Gemini en utilisant les sources disponibles.

    Attributes:
        vault_path: Chemin racine du vault Obsidian.
        wiki_path: Chemin du répertoire 02_WIKI/.
        raw_path: Chemin du répertoire 00_RAW/.
        model_name: Nom du modèle Gemini utilisé.
    """

    def __init__(self) -> None:
        """Initialise l'enricher avec la configuration courante."""
        settings = get_settings()
        self.vault_path = Path(settings.get_vault_path())
        self.wiki_path = self.vault_path / "02_WIKI"
        self.raw_path = self.vault_path / "00_RAW"
        self._settings = settings
        self.model_name = settings.gemini_model_wiki
        # Index en mémoire pour lookups O(1) au lieu de 3 scans complets
        self._concept_manager = ConceptManager()

    def suggest_missing_connections(self) -> list[tuple[str, str]]:
        """Suggère des connexions entre concepts qui ne sont pas encore liés.

        Algorithme (sans LLM) :
        1. Pour chaque fiche, lire ses sources (articles) et ses concepts liés
        2. Pour chaque paire de fiches partageant ≥2 sources communes,
           suggérer un lien si pas encore présent
        3. Retourner liste de (concept_a, concept_b) à lier

        Returns:
            Liste de tuples (concept_a, concept_b) représentant les connexions
            suggérées entre fiches non encore liées.
        """
        if not self.wiki_path.exists():
            logger.warning(f"Répertoire wiki introuvable : {self.wiki_path}")
            return []

        # Collecter les sources et liens existants pour chaque fiche
        fiche_sources: dict[str, set[str]] = {}  # stem → set de sources
        fiche_links: dict[str, set[str]] = {}  # stem → set de liens existants

        for md_file in self.wiki_path.rglob("*.md"):
            if md_file.stem.startswith("000_"):
                continue
            try:
                post = frontmatter.load(str(md_file))
                # Sources depuis le frontmatter
                sources_raw = post.metadata.get("sources", [])
                if isinstance(sources_raw, list):
                    sources = {str(s) for s in sources_raw if s}
                else:
                    sources = set()

                # Liens existants depuis le contenu (wikilinks)
                wikilinks = re.findall(r"\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]", post.content or "")
                links = {link.strip().lower() for link in wikilinks}

                fiche_sources[md_file.stem] = sources
                fiche_links[md_file.stem] = links
            except Exception as e:
                logger.debug(f"Erreur lecture {md_file.name} : {e}")

        # Trouver les paires avec ≥2 sources communes non encore liées
        suggestions: list[tuple[str, str]] = []
        stems = list(fiche_sources.keys())

        for i, stem_a in enumerate(stems):
            for stem_b in stems[i + 1 :]:
                sources_a = fiche_sources[stem_a]
                sources_b = fiche_sources[stem_b]

                # Vérifier les sources communes
                common_sources = sources_a & sources_b
                if len(common_sources) < 2:
                    continue

                # Vérifier si le lien existe déjà (dans les deux sens)
                links_a = fiche_links.get(stem_a, set())
                links_b = fiche_links.get(stem_b, set())
                already_linked = stem_b.lower() in links_a or stem_a.lower() in links_b

                if not already_linked:
                    suggestions.append((stem_a, stem_b))
                    logger.debug(
                        f"Connexion suggérée : {stem_a} ↔ {stem_b} "
                        f"({len(common_sources)} sources communes)"
                    )

        logger.info(f"Connexions suggérées : {len(suggestions)}")
        return suggestions

    def enrich_concept(self, concept_name: str) -> bool:
        """Enrichit une fiche concept avec Gemini.

        Pipeline :
        1. Lire la fiche existante
        2. Lire toutes les sources mentionnées dans la fiche
        3. Appeler Gemini pour enrichir la définition et suggérer des connexions
        4. Mettre à jour la fiche (section Définition + Questions ouvertes)
        5. Retourner True si enrichissement réussi

        Args:
            concept_name: Nom du concept à enrichir (stem du fichier ou titre).

        Returns:
            True si l'enrichissement a réussi, False sinon.
        """
        # Trouver la fiche
        concept_path = self._find_concept_file(concept_name)
        if concept_path is None:
            logger.error(f"Fiche introuvable pour : {concept_name!r}")
            return False

        logger.info(f"Enrichissement de : {concept_path.name}")

        # Lire la fiche existante
        try:
            post = frontmatter.load(str(concept_path))
        except Exception as e:
            logger.error(f"Impossible de lire {concept_path.name} : {e}")
            return False

        current_content = post.content or ""

        # Lire les sources mentionnées dans la fiche
        sources_content = self._load_sources_content(post)

        if not sources_content:
            logger.warning(f"Aucune source trouvée pour {concept_path.name}")
            sources_content = "Aucune source disponible."

        # Appeler Gemini pour enrichir
        try:
            enriched_content = self._call_gemini_enrich(
                concept_name=concept_name,
                current_content=current_content,
                sources_content=sources_content,
            )
        except RuntimeError as e:
            logger.error(f"Erreur Gemini enrichissement : {e}")
            return False

        # Mettre à jour la fiche
        post.content = enriched_content
        try:
            concept_path.write_text(frontmatter.dumps(post), encoding="utf-8")
            logger.info(f"Fiche enrichie : {concept_path.name}")
            return True
        except OSError as e:
            logger.error(f"Impossible d'écrire {concept_path.name} : {e}")
            return False

    def enrich_all_async(
        self,
        missing: list,
        concurrency: int = 5,
    ) -> EnrichBatchResult:
        """Enrichit toutes les fiches avec définitions manquantes en parallèle.

        Lance N appels Gemini simultanément via asyncio, contrôlés par un semaphore.

        Args:
            missing: Liste de MissingDefinition (depuis HealthChecker.check_missing_definitions).
            concurrency: Nombre de requêtes Gemini simultanées (défaut: 5).

        Returns:
            EnrichBatchResult avec l'agrégat des résultats.
        """
        return asyncio.run(self._enrich_all_async_inner(missing, concurrency=concurrency))

    async def _enrich_all_async_inner(
        self,
        missing: list,
        concurrency: int,
    ) -> EnrichBatchResult:
        """Implémentation interne async de l'enrichissement en masse.

        Args:
            missing: Liste de MissingDefinition à enrichir.
            concurrency: Nombre de requêtes simultanées.

        Returns:
            EnrichBatchResult agrégé.
        """
        try:
            from google import genai as _genai
        except ImportError as e:
            raise RuntimeError("google-genai non installé. Lancez : uv sync") from e

        api_key = self._settings.get_gemini_api_key()
        if not api_key:
            raise RuntimeError("Clé API Gemini non configurée (GEMINI_API_KEY_2)")

        semaphore = asyncio.Semaphore(concurrency)
        batch_result = EnrichBatchResult()
        lock = asyncio.Lock()

        async def _process_one(item) -> EnrichResult:
            result = EnrichResult(concept_name=item.title)
            concept_path = item.path

            try:
                post = frontmatter.load(str(concept_path))
            except Exception as e:
                result.error = f"Lecture impossible : {e}"
                return result

            current_content = post.content or ""
            sources_content = self._load_sources_content(post) or "Aucune source disponible."

            prompt = ENRICH_PROMPT.format(
                current_content=current_content,
                sources_content=sources_content[:MAX_SOURCE_CHARS],
            )

            # Appel Gemini async avec semaphore
            async with semaphore:
                loop = asyncio.get_event_loop()
                client = _genai.Client(api_key=api_key)

                for attempt in range(1, MAX_RETRIES + 1):
                    try:
                        response = await loop.run_in_executor(
                            None,
                            lambda p=prompt: client.models.generate_content(
                                model=self.model_name, contents=p
                            ),
                        )
                        enriched_text = response.text

                        # Compter les tokens si disponibles
                        try:
                            result.input_tokens = response.usage_metadata.prompt_token_count or 0
                            result.output_tokens = (
                                response.usage_metadata.candidates_token_count or 0
                            )
                        except Exception:
                            pass

                        break
                    except Exception as e:
                        if attempt < MAX_RETRIES:
                            logger.warning(
                                f"Gemini enrich tentative {attempt}/{MAX_RETRIES} "
                                f"({item.title}) : {e}. Retry dans {RETRY_DELAY_S}s..."
                            )
                            await asyncio.sleep(RETRY_DELAY_S)
                        else:
                            result.error = f"Gemini échoué après {MAX_RETRIES} tentatives : {e}"
                            return result

            # Écriture protégée par lock (évite les race conditions sur le FS)
            async with lock:
                try:
                    post.content = enriched_text
                    concept_path.write_text(frontmatter.dumps(post), encoding="utf-8")
                    result.success = True
                    logger.debug(f"Fiche enrichie : {concept_path.name}")
                except OSError as e:
                    result.error = f"Écriture impossible : {e}"

            return result

        tasks = [_process_one(item) for item in missing]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for res in results:
            if isinstance(res, Exception):
                err = EnrichResult(concept_name="unknown", error=f"Exception inattendue : {res}")
                batch_result.results.append(err)
            else:
                batch_result.results.append(res)

        return batch_result

    def _call_gemini_enrich(
        self,
        concept_name: str,
        current_content: str,
        sources_content: str,
    ) -> str:
        """Appelle Gemini pour enrichir le contenu d'une fiche.

        Args:
            concept_name: Nom du concept à enrichir.
            current_content: Contenu actuel de la fiche (sans frontmatter).
            sources_content: Contenu agrégé des sources.

        Returns:
            Contenu markdown enrichi.

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
        prompt = ENRICH_PROMPT.format(
            current_content=current_content,
            sources_content=sources_content[:MAX_SOURCE_CHARS],
        )

        last_error: Exception | None = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = client.models.generate_content(model=self.model_name, contents=prompt)
                return response.text
            except Exception as e:
                last_error = e
                if attempt < MAX_RETRIES:
                    logger.warning(
                        f"Gemini enrich tentative {attempt}/{MAX_RETRIES} échouée : {e}. "
                        f"Retry dans {RETRY_DELAY_S}s..."
                    )
                    time.sleep(RETRY_DELAY_S)
                else:
                    logger.error(f"Gemini enrich : {MAX_RETRIES} tentatives épuisées.")

        raise RuntimeError(
            f"Appel Gemini enrich échoué après {MAX_RETRIES} tentatives"
        ) from last_error

    def _find_concept_file(self, concept_name: str) -> Path | None:
        """Recherche la fiche wiki correspondant à un nom de concept.

        Utilise l'index en mémoire du ConceptManager pour un lookup O(1)
        au lieu de 3 scans complets du répertoire wiki.

        Args:
            concept_name: Nom du concept à rechercher.

        Returns:
            Chemin de la fiche trouvée, ou None si introuvable.
        """
        return self._concept_manager.find_fiche_by_name(concept_name)

    def _load_sources_content(self, post: frontmatter.Post) -> str:
        """Charge le contenu des sources mentionnées dans une fiche.

        Lit les articles RAW référencés dans le frontmatter (champ sources).

        Args:
            post: Objet frontmatter de la fiche wiki.

        Returns:
            Contenu agrégé des sources (tronqué à MAX_SOURCE_CHARS).
        """
        sources_raw = post.metadata.get("sources", [])
        if not isinstance(sources_raw, list):
            return ""

        parts: list[str] = []
        total_chars = 0

        for source_stem in sources_raw:
            if total_chars >= MAX_SOURCE_CHARS:
                break
            # Chercher le fichier source dans 00_RAW/
            source_file = self._find_raw_file(str(source_stem))
            if source_file is None:
                continue
            try:
                source_post = frontmatter.load(str(source_file))
                content = source_post.content or ""
                remaining = MAX_SOURCE_CHARS - total_chars
                if len(content) > remaining:
                    content = content[:remaining] + "\n[... tronqué ...]"
                parts.append(f"### Source : {source_stem}\n{content}")
                total_chars += len(content)
            except Exception as e:
                logger.debug(f"Impossible de lire source {source_stem} : {e}")

        return "\n\n---\n\n".join(parts)

    def _find_raw_file(self, stem: str) -> Path | None:
        """Recherche un fichier RAW par son stem.

        Args:
            stem: Stem du fichier à rechercher dans 00_RAW/.

        Returns:
            Chemin du fichier trouvé, ou None si introuvable.
        """
        if not self.raw_path.exists():
            return None

        # Recherche exacte
        for md_file in self.raw_path.rglob("*.md"):
            if md_file.stem == stem:
                return md_file

        # Recherche insensible à la casse
        stem_lower = stem.lower()
        for md_file in self.raw_path.rglob("*.md"):
            if md_file.stem.lower() == stem_lower:
                return md_file

        return None
