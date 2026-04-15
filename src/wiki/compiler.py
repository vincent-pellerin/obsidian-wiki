"""Compilateur wiki — transforme les articles bruts en fiches concepts.

Orchestre le pipeline complet pour chaque article :
  1. Lecture du fichier RAW (markdown + frontmatter)
  2. Extraction des concepts via Gemini LLM
  3. Création/mise à jour des fiches dans 02_WIKI/
  4. Ajout des backlinks bidirectionnels
  5. Mise à jour de l'index maître
  6. Append au journal log.md

Trois modes de compilation :
  - Synchrone (défaut) : un article à la fois via generate_content
  - Async concurrent (--async) : N requêtes en parallèle via asyncio + semaphore
  - Batch API (--batch) : soumission groupée via client.batches.create,
    résultats récupérés après completion (50% moins cher)
"""

import asyncio
import logging
import re
import threading
import time
from datetime import date
from pathlib import Path

import frontmatter
import yaml

from src.config import get_settings
from src.wiki.cache import WikiStateCache
from src.wiki.concept_manager import ConceptManager
from src.wiki.indexer import Indexer
from src.wiki.linker import Linker
from src.wiki.models import (
    BatchCompilationResult,
    CompilationResult,
    ConceptData,
    ExtractedKnowledge,
    PersonData,
    TechData,
    TopicData,
)

logger = logging.getLogger(__name__)

# Longueur maximale du contenu envoyé au LLM (en caractères)
# Couvre ~95% des articles ; les articles plus longs sont tronqués proprement
MAX_ARTICLE_CHARS = 20_000

# Longueur maximale pour les articles longform (00_RAW/articles/longform/)
MAX_ARTICLE_CHARS_LONGFORM = 80_000

# Taille minimale de contenu utile (en caractères) pour qu'un article soit compilable
# En dessous : CAPTCHA, 403, page vide, etc.
MIN_ARTICLE_CHARS = 500

# Patterns indiquant un article invalide (CAPTCHA, erreur d'extraction)
_INVALID_CONTENT_PATTERNS = [
    "performing security verification",
    "security service to protect against malicious bots",
    "please enable javascript",
    "just a moment",
    "403 forbidden",
    "404 not found",
    "access denied",
    "enable cookies",
    "verify you are human",
    "ddos protection",
    "checking your browser",
]

# Nombre max de tentatives pour l'appel Gemini
MAX_RETRIES = 3
RETRY_DELAY_S = 5.0

# Paramètres Batch API
BATCH_POLL_INTERVAL_S = 30
BATCH_COMPLETED_STATES = {
    "JOB_STATE_SUCCEEDED",
    "JOB_STATE_FAILED",
    "JOB_STATE_CANCELLED",
    "JOB_STATE_EXPIRED",
}

# En-tête du journal log.md (créé si le fichier n'existe pas)
_LOG_HEADER = """\
# Journal des opérations du wiki

> Ce fichier est auto-maintenu par le système. Ne pas modifier manuellement.
> Format : `## [YYYY-MM-DD] opération | titre` — parseable par `grep`.
"""


def append_log_entry(
    vault_path: Path,
    operation: str,
    title: str,
    details: dict[str, str | int] | None = None,
) -> Path:
    """Ajoute une entrée au journal log.md du vault.

    Le fichier est créé avec un en-tête s'il n'existe pas. L'entrée est
    toujours ajoutée à la fin (append-only).

    Args:
        vault_path: Chemin racine du vault Obsidian.
        operation: Type d'opération (compile, ingest, lint, query).
        title: Titre court de l'opération.
        details: Dictionnaire optionnel de détails clé-valeur.

    Returns:
        Chemin du fichier log.md.
    """
    log_path = vault_path / "02_WIKI" / "log.md"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    today = date.today().isoformat()
    lines: list[str] = [
        "",
        f"## [{today}] {operation} | {title}",
    ]

    if details:
        for key, value in details.items():
            lines.append(f"- {key} : {value}")

    entry = "\n".join(lines) + "\n"

    if log_path.exists():
        # Append au fichier existant
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(entry)
    else:
        # Créer le fichier avec l'en-tête
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(_LOG_HEADER)
            f.write(entry)

    logger.info(f"Entrée log.md ajoutée : [{today}] {operation} | {title}")
    return log_path


CONCEPT_EXTRACTION_PROMPT = """\
Analyse cet article et extrais les connaissances structurées.
Retourne UNIQUEMENT du YAML valide, sans balises markdown, sans commentaires.

Format attendu :
concepts:
  - name: "Nom du concept"
    definition: "Définition autonome et complète (2-3 phrases). Doit être compréhensible sans lire l'article."
    context: "Comment ce concept est spécifiquement utilisé ou illustré dans cet article"
    related:
      - "Concept lié A"
      - "Concept lié B"
    questions:
      - "Question ouverte ou point à approfondir sur ce concept"
    aliases: []
people:
  - name: "Prénom Nom"
    role: "Titre professionnel précis (ex: 'Chercheur en ML chez Google', 'Fondateur de OpenAI')"
    bio: "Biographie courte (1-2 phrases) : qui est cette personne, pourquoi est-elle notable"
    context: "Pourquoi cette personne est mentionnée dans cet article"
    related:
      - "Concept, technologie ou personne liée A"
      - "Concept, technologie ou personne liée B"
technologies:
  - name: "Nom outil/techno"
    type: "database|framework|library|platform|language|tool|service"
    description: "Description technique autonome (1-2 phrases) : ce que c'est, à quoi ça sert"
    context: "Comment cet outil est utilisé ou mentionné dans cet article"
    related:
      - "Technologie alternative ou complémentaire A"
      - "Concept ou écosystème lié B"
    questions:
      - "Question technique ouverte sur cet outil (comparaison, limite, cas d'usage)"
topics:
  - name: "Sujet principal"
    definition: "Description du sujet (2-3 phrases) : de quoi il s'agit, pourquoi c'est important"
    related:
      - "sujet lié 1"
      - "sujet lié 2"

Règles strictes :
- 3 à 8 concepts clés maximum (qualité > quantité)
- 1 à 3 topics maximum (les sujets les plus importants de l'article)
- Uniquement les éléments réellement présents et significatifs dans l'article
- Toutes les définitions/descriptions en français
- Les définitions doivent être AUTONOMES : compréhensibles sans contexte de l'article
- Ne pas dupliquer : si un concept est déjà un topic, ne pas le mettre dans les deux
- Noms propres (personnes, outils) en langue d'origine
- Si une catégorie est vide, mettre une liste vide []
- Pour les personnes : ne pas créer de fiche si le rôle est générique (ex: "auteur", "co-auteur", "abonné")

Article :
{article_content}
"""


def _is_invalid_content(content: str) -> str | None:
    """Détecte si le contenu d'un article est invalide (CAPTCHA, 403, page vide).

    Args:
        content: Contenu brut de l'article.

    Returns:
        Message d'erreur si invalide, None si contenu valide.
    """
    stripped = content.strip()

    if len(stripped) < MIN_ARTICLE_CHARS:
        return f"Contenu trop court ({len(stripped)} chars < {MIN_ARTICLE_CHARS} minimum)"

    lower = stripped.lower()
    for pattern in _INVALID_CONTENT_PATTERNS:
        if pattern in lower:
            return f"Contenu invalide détecté : '{pattern}'"

    return None


def _strip_yaml_fences(text: str) -> str:
    """Supprime les balises ```yaml ... ``` ou ``` ... ``` si présentes.

    Args:
        text: Texte brut retourné par le LLM.

    Returns:
        Texte YAML nettoyé.
    """
    text = text.strip()
    # Supprimer ```yaml ou ```
    text = re.sub(r"^```(?:yaml)?\n?", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n?```$", "", text, flags=re.MULTILINE)
    return text.strip()


def _parse_gemini_response(raw_text: str) -> ExtractedKnowledge:
    """Parse la réponse YAML de Gemini en ExtractedKnowledge.

    Args:
        raw_text: Texte brut retourné par le LLM.

    Returns:
        ExtractedKnowledge avec les données parsées.
        Retourne un objet vide en cas d'erreur de parsing.
    """
    cleaned = _strip_yaml_fences(raw_text)

    try:
        data = yaml.safe_load(cleaned)
    except yaml.YAMLError as e:
        logger.error(f"Erreur parsing YAML Gemini : {e}\nContenu:\n{cleaned[:500]}")
        return ExtractedKnowledge()

    if not isinstance(data, dict):
        logger.warning(f"Réponse Gemini inattendue (non-dict) : {type(data)}")
        return ExtractedKnowledge()

    knowledge = ExtractedKnowledge()

    # Concepts
    for item in data.get("concepts") or []:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        knowledge.concepts.append(
            ConceptData(
                name=str(item["name"]),
                definition=str(item.get("definition", "")),
                context=str(item.get("context", "")),
                aliases=list(item.get("aliases") or []),
                related=list(item.get("related") or []),
                questions=list(item.get("questions") or []),
            )
        )

    # People — ignorer les rôles génériques sans valeur encyclopédique
    _GENERIC_ROLES = {
        "auteur",
        "auteure",
        "author",
        "co-auteur",
        "co-auteure",
        "co-author",
        "abonné",
        "abonnée",
        "subscriber",
        "abonné payant",
        "abonnée payante",
        "photographe",
        "photographer",
        "soutien",
        "supporter",
        "invité",
        "invitée",
        "guest",
        "personnage fictif",
        "fictional character",
        "personnage philosophique",
    }
    for item in data.get("people") or []:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        role = str(item.get("role", "")).strip().lower()
        bio = str(item.get("bio", "")).strip()
        # Ignorer si rôle générique ET pas de bio substantielle
        if role in _GENERIC_ROLES and len(bio) < 30:
            logger.debug(f"Personne ignorée (rôle générique) : {item['name']} ({role})")
            continue
        knowledge.people.append(
            PersonData(
                name=str(item["name"]),
                role=str(item.get("role", "")),
                bio=bio,
                context=str(item.get("context", "")),
                related=list(item.get("related") or []),
            )
        )

    # Technologies
    for item in data.get("technologies") or []:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        knowledge.technologies.append(
            TechData(
                name=str(item["name"]),
                type=str(item.get("type", "tool")),
                description=str(item.get("description", "")),
                context=str(item.get("context", "")),
                related=list(item.get("related") or []),
                questions=list(item.get("questions") or []),
            )
        )

    # Topics
    for item in data.get("topics") or []:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        knowledge.topics.append(
            TopicData(
                name=str(item["name"]),
                definition=str(item.get("definition", "")),
                related=list(item.get("related") or []),
            )
        )

    return knowledge


def _call_gemini(
    content: str,
    model_name: str,
    api_key: str,
    max_chars: int = MAX_ARTICLE_CHARS,
) -> tuple[str, int, int]:
    """Appelle l'API Gemini pour extraire les concepts d'un article.

    Args:
        content: Contenu de l'article à analyser.
        model_name: Nom du modèle Gemini.
        api_key: Clé API Gemini.
        max_chars: Nombre maximum de caractères envoyés au LLM.

    Returns:
        Tuple (texte_réponse, input_tokens, output_tokens).

    Raises:
        RuntimeError: Si toutes les tentatives ont échoué.
    """
    try:
        from google import genai
    except ImportError as e:
        raise RuntimeError("google-genai non installé. Lancez : uv sync") from e

    client = genai.Client(api_key=api_key)
    prompt = CONCEPT_EXTRACTION_PROMPT.format(article_content=content[:max_chars])

    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client.models.generate_content(model=model_name, contents=prompt)
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


async def _call_gemini_async(
    content: str,
    model_name: str,
    api_key: str,
    semaphore: asyncio.Semaphore,
    max_chars: int = MAX_ARTICLE_CHARS,
) -> tuple[str, int, int]:
    """Appelle l'API Gemini de façon asynchrone avec contrôle de concurrence.

    Args:
        content: Contenu de l'article à analyser.
        model_name: Nom du modèle Gemini.
        api_key: Clé API Gemini.
        semaphore: Semaphore asyncio pour limiter la concurrence.
        max_chars: Nombre maximum de caractères envoyés au LLM.

    Returns:
        Tuple (texte_réponse, input_tokens, output_tokens).

    Raises:
        RuntimeError: Si toutes les tentatives ont échoué.
    """
    try:
        from google import genai
    except ImportError as e:
        raise RuntimeError("google-genai non installé. Lancez : uv sync") from e

    client = genai.Client(api_key=api_key)
    prompt = CONCEPT_EXTRACTION_PROMPT.format(article_content=content[:max_chars])

    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            async with semaphore:
                response = await client.aio.models.generate_content(
                    model=model_name,
                    contents=prompt,
                )
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
                    f"Gemini async tentative {attempt}/{MAX_RETRIES} échouée : {e}. "
                    f"Retry dans {RETRY_DELAY_S}s..."
                )
                await asyncio.sleep(RETRY_DELAY_S)
            else:
                logger.error(f"Gemini async : {MAX_RETRIES} tentatives épuisées.")

    raise RuntimeError(f"Appel Gemini async échoué après {MAX_RETRIES} tentatives") from last_error


class WikiCompiler:
    """Compile les articles RAW en fiches wiki structurées.

    Orchestre le pipeline complet : extraction LLM → ConceptManager
    → Linker → Indexer.

    Attributes:
        concept_manager: Gestion CRUD des fiches wiki.
        linker: Gestion des backlinks.
        indexer: Génération de l'index maître.
        vault_path: Chemin racine du vault.
        model_name: Nom du modèle Gemini utilisé.
    """

    def __init__(self, model_override: str | None = None) -> None:
        """Initialise le compilateur avec la configuration courante.

        Args:
            model_override: Nom de modèle Gemini à utiliser à la place de
                celui défini dans la configuration (utile pour les tests).
        """
        settings = get_settings()
        self.vault_path = Path(settings.get_vault_path())
        self._settings = settings
        self.model_name = model_override or settings.gemini_model_wiki
        self.cache = WikiStateCache(self.vault_path)

        # Reconstruction automatique du cache au premier lancement
        if self.cache.is_empty():
            logger.info("Premier lancement : reconstruction du cache wiki...")
            self.cache.rebuild_all()

        # Partager le cache avec les sous-composants
        self.concept_manager = ConceptManager(cache=self.cache)
        self.linker = Linker(cache=self.cache)
        self.indexer = Indexer()

        # Lock pour protéger _process_extraction_result en mode async concurrent
        # (ConceptManager et Linker ne sont pas thread-safe)
        self._process_lock = threading.Lock()

        if model_override:
            logger.info(
                f"Modèle override : {model_override} (config : {settings.gemini_model_wiki})"
            )

    def compile_article(
        self,
        raw_path: Path,
        *,
        force: bool = False,
        max_chars: int = MAX_ARTICLE_CHARS,
    ) -> CompilationResult:
        """Compile un article RAW en fiches wiki.

        Pipeline :
        1. Vérifie si l'article a déjà été compilé (sauf force=True)
        2. Lit le contenu et le titre de l'article
        3. Appelle Gemini pour extraire les connaissances
        4. Crée/met à jour les fiches pour chaque entité extraite
        5. Ajoute les backlinks dans l'article et les fiches
        6. Marque l'article comme compilé

        Args:
            raw_path: Chemin du fichier article dans 00_RAW/.
            force: Si True, recompile même si déjà marqué.

        Returns:
            CompilationResult avec les statistiques de compilation.
        """
        result = CompilationResult(article_path=raw_path)

        # Lecture et vérification
        try:
            post = frontmatter.load(str(raw_path))
        except Exception as e:
            result.errors.append(f"Lecture frontmatter impossible : {e}")
            return result

        result.article_title = str(post.metadata.get("title", raw_path.stem))

        # Skip si déjà compilé
        if post.metadata.get("wiki_compiled") and not force:
            result.skipped = True
            logger.debug(f"Ignoré (déjà compilé) : {raw_path.name}")
            return result

        # Appel Gemini
        article_content = post.content or ""
        if not article_content.strip():
            result.errors.append("Article vide, skip")
            return result

        # Détecter les articles invalides (CAPTCHA, 403, page vide)
        invalid_reason = _is_invalid_content(article_content)
        if invalid_reason:
            result.skipped = True
            logger.warning(f"Article invalide ignoré : {raw_path.name} — {invalid_reason}")
            return result

        logger.info(
            f"Compilation : {raw_path.name} ({len(article_content)} chars, max={max_chars:,})"
        )

        try:
            raw_response, input_tokens, output_tokens = _call_gemini(
                content=article_content,
                model_name=self.model_name,
                api_key=self._settings.get_gemini_api_key(),
                max_chars=max_chars,
            )
            result.input_tokens = input_tokens
            result.output_tokens = output_tokens
        except RuntimeError as e:
            result.errors.append(f"Gemini : {e}")
            return result

        logger.debug(f"Réponse brute Gemini ({len(raw_response)} chars) :\n{raw_response[:2000]}")

        # Traiter le résultat via la méthode partagée
        self._process_extraction_result(
            raw_text=raw_response,
            raw_path=raw_path,
            article_title=result.article_title,
            result=result,
        )

        # Copier les tokens (définis avant _process_extraction_result)
        result.input_tokens = input_tokens
        result.output_tokens = output_tokens

        return result

    def batch_compile(
        self,
        source: str = "all",
        limit: int | None = None,
        *,
        force: bool = False,
        rebuild_index: bool = True,
        max_chars: int | None = None,
    ) -> BatchCompilationResult:
        """Compile tous les articles RAW d'une source donnée.

        Args:
            source: Source à compiler ("medium", "substack", "all" ou "longform").
            limit: Nombre maximum d'articles à traiter.
            force: Si True, recompile les articles déjà compilés.
            rebuild_index: Si True, régénère l'index maître à la fin.
            max_chars: Limite de caractères envoyés au LLM (défaut selon source).

        Returns:
            BatchCompilationResult avec l'agrégat des résultats.
        """
        effective_max_chars = max_chars or (
            MAX_ARTICLE_CHARS_LONGFORM if source == "longform" else MAX_ARTICLE_CHARS
        )
        articles = self._collect_articles(source)
        if limit:
            articles = articles[:limit]

        logger.info(
            f"Batch compile : {len(articles)} articles (source={source}, max_chars={effective_max_chars:,})"
        )

        batch = BatchCompilationResult()
        for i, article_path in enumerate(articles, 1):
            logger.info(f"[{i}/{len(articles)}] {article_path.name}")
            result = self.compile_article(article_path, force=force, max_chars=effective_max_chars)
            batch.results.append(result)

        if rebuild_index and batch.total_compiled > 0:
            try:
                self.indexer.build_master_index()
            except Exception as e:
                logger.error(f"Erreur génération index : {e}")

        # Persister le cache après le batch
        if batch.total_compiled > 0:
            self.cache.save()

        # Append au journal log.md
        if batch.total_compiled > 0:
            try:
                append_log_entry(
                    vault_path=self.vault_path,
                    operation="compile",
                    title=f"Batch {batch.total_compiled} articles",
                    details={
                        "Source": source,
                        "Fiches créées": batch.total_concepts_created,
                        "Fiches mises à jour": batch.total_concepts_updated,
                        "Erreurs": batch.total_errors,
                        "Tokens input": batch.total_input_tokens,
                        "Tokens output": batch.total_output_tokens,
                    },
                )
            except OSError as e:
                logger.warning(f"Impossible d'écrire dans log.md : {e}")

        logger.info(batch.summary())
        return batch

    # ------------------------------------------------------------------
    # Mode Async Concurrent
    # ------------------------------------------------------------------

    def async_batch_compile(
        self,
        source: str = "all",
        limit: int | None = None,
        *,
        force: bool = False,
        concurrency: int = 15,
        rebuild_index: bool = True,
        max_chars: int | None = None,
    ) -> BatchCompilationResult:
        """Compile les articles en parallèle via asyncio (mode async concurrent).

        Lance N requêtes Gemini simultanément, contrôlées par un semaphore.
        Beaucoup plus rapide que le mode séquentiel (10-30x selon la concurrence).

        Args:
            source: Source à compiler ("medium", "substack", "all" ou "longform").
            limit: Nombre maximum d'articles à traiter.
            force: Si True, recompile les articles déjà compilés.
            concurrency: Nombre de requêtes Gemini simultanées (défaut: 15).
            rebuild_index: Si True, régénère l'index maître à la fin.
            max_chars: Limite de caractères envoyés au LLM (défaut selon source).

        Returns:
            BatchCompilationResult avec l'agrégat des résultats.
        """
        effective_max_chars = max_chars or (
            MAX_ARTICLE_CHARS_LONGFORM if source == "longform" else MAX_ARTICLE_CHARS
        )
        return asyncio.run(
            self._async_batch_compile_inner(
                source=source,
                limit=limit,
                force=force,
                concurrency=concurrency,
                rebuild_index=rebuild_index,
                max_chars=effective_max_chars,
            )
        )

    async def _async_batch_compile_inner(
        self,
        source: str,
        limit: int | None,
        *,
        force: bool,
        concurrency: int,
        rebuild_index: bool,
        max_chars: int = MAX_ARTICLE_CHARS,
    ) -> BatchCompilationResult:
        """Implémentation interne async du mode concurrent.

        Args:
            source: Source à compiler.
            limit: Nombre maximum d'articles.
            force: Recompiler les articles déjà compilés.
            concurrency: Nombre de requêtes simultanées.
            rebuild_index: Régénérer l'index maître.
            max_chars: Limite de caractères envoyés au LLM.

        Returns:
            BatchCompilationResult agrégé.
        """
        articles = self._collect_articles(source)
        if limit:
            articles = articles[:limit]

        # Filtrer les articles déjà compilés (sauf force)
        pending: list[tuple[Path, str, str]] = []
        for article_path in articles:
            try:
                post = frontmatter.load(str(article_path))
            except Exception as e:
                logger.warning(f"Lecture impossible {article_path.name} : {e}")
                continue

            if post.metadata.get("wiki_compiled") and not force:
                logger.debug(f"Ignoré (déjà compilé) : {article_path.name}")
                continue

            content = post.content or ""
            if not content.strip():
                logger.warning(f"Article vide, ignoré : {article_path.name}")
                continue

            # Détecter les articles invalides (CAPTCHA, 403, page vide)
            invalid_reason = _is_invalid_content(content)
            if invalid_reason:
                logger.warning(f"Article invalide ignoré : {article_path.name} — {invalid_reason}")
                continue

            title = str(post.metadata.get("title", article_path.stem))
            pending.append((article_path, title, content))

        if not pending:
            logger.info("Aucun article à compiler.")
            return BatchCompilationResult()

        logger.info(
            f"Async compile : {len(pending)} articles | concurrence={concurrency} | "
            f"max_chars={max_chars:,} (source={source})"
        )

        api_key = self._settings.get_gemini_api_key()
        semaphore = asyncio.Semaphore(concurrency)
        batch_result = BatchCompilationResult()

        async def _process_one(article_path: Path, title: str, content: str) -> CompilationResult:
            """Traite un article : appel Gemini async + traitement synchrone protégé."""
            result = CompilationResult(article_path=article_path, article_title=title)
            try:
                raw_response, input_tokens, output_tokens = await _call_gemini_async(
                    content=content,
                    model_name=self.model_name,
                    api_key=api_key,
                    semaphore=semaphore,
                    max_chars=max_chars,
                )
                result.input_tokens = input_tokens
                result.output_tokens = output_tokens
            except RuntimeError as e:
                result.errors.append(f"Gemini : {e}")
                return result

            # _process_extraction_result modifie des fichiers et le cache —
            # on le protège avec un lock pour éviter les race conditions
            with self._process_lock:
                self._process_extraction_result(
                    raw_text=raw_response,
                    raw_path=article_path,
                    article_title=title,
                    result=result,
                )
                result.input_tokens = input_tokens
                result.output_tokens = output_tokens

            return result

        # Lancer toutes les tâches en parallèle
        tasks = [_process_one(path, title, content) for path, title, content in pending]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for res in results:
            if isinstance(res, Exception):
                # Erreur inattendue non capturée dans _process_one
                err_result = CompilationResult(article_path=Path("unknown"))
                err_result.errors.append(f"Exception inattendue : {res}")
                batch_result.results.append(err_result)
            else:
                batch_result.results.append(res)

        # Persister cache + index + log
        if batch_result.total_compiled > 0:
            if rebuild_index:
                try:
                    self.indexer.build_master_index()
                except Exception as e:
                    logger.error(f"Erreur génération index : {e}")

            self.cache.save()

            try:
                append_log_entry(
                    vault_path=self.vault_path,
                    operation="compile-async",
                    title=f"Async {batch_result.total_compiled} articles",
                    details={
                        "Source": source,
                        "Concurrence": concurrency,
                        "Fiches créées": batch_result.total_concepts_created,
                        "Fiches mises à jour": batch_result.total_concepts_updated,
                        "Erreurs": batch_result.total_errors,
                        "Tokens input": batch_result.total_input_tokens,
                        "Tokens output": batch_result.total_output_tokens,
                    },
                )
            except OSError as e:
                logger.warning(f"Impossible d'écrire dans log.md : {e}")

        logger.info(batch_result.summary())
        return batch_result

    # ------------------------------------------------------------------
    # Mode Batch API Gemini
    # ------------------------------------------------------------------

    def batch_compile_api(
        self,
        source: str = "all",
        limit: int | None = None,
        *,
        force: bool = False,
        rebuild_index: bool = True,
    ) -> BatchCompilationResult:
        """Compile les articles via la Gemini Batch API (50% moins cher).

        Soumet toutes les requêtes en un seul batch job, puis récupère
        les résultats après completion pour traiter les fiches wiki.

        Args:
            source: Source à compiler ("medium", "substack", ou "all").
            limit: Nombre maximum d'articles à traiter.
            force: Si True, recompile les articles déjà compilés.
            rebuild_index: Si True, régénère l'index maître à la fin.

        Returns:
            BatchCompilationResult avec l'agrégat des résultats.

        Raises:
            RuntimeError: Si la soumission ou la récupération échoue.
        """
        try:
            from google import genai
        except ImportError as e:
            raise RuntimeError("google-genai non installé. Lancez : uv sync") from e

        # 1. Collecter les articles à compiler
        articles = self._collect_articles(source)
        if limit:
            articles = articles[:limit]

        # Filtrer les articles déjà compilés (sauf force)
        pending: list[tuple[Path, str, str]] = []  # (path, title, content)
        for article_path in articles:
            try:
                post = frontmatter.load(str(article_path))
            except Exception as e:
                logger.warning(f"Lecture impossible {article_path.name} : {e}")
                continue

            if post.metadata.get("wiki_compiled") and not force:
                logger.debug(f"Ignoré (déjà compilé) : {article_path.name}")
                continue

            content = post.content or ""
            if not content.strip():
                logger.warning(f"Article vide, ignoré : {article_path.name}")
                continue

            # Détecter les articles invalides (CAPTCHA, 403, page vide)
            invalid_reason = _is_invalid_content(content)
            if invalid_reason:
                logger.warning(f"Article invalide ignoré : {article_path.name} — {invalid_reason}")
                continue

            title = str(post.metadata.get("title", article_path.stem))
            pending.append((article_path, title, content))

        if not pending:
            logger.info("Aucun article à compiler.")
            return BatchCompilationResult()

        logger.info(f"Batch API : {len(pending)} articles à compiler (source={source})")

        # 2. Construire les requêtes inline avec le bon format Pydantic
        from google.genai import types as genai_types

        inline_requests: list[genai_types.InlinedRequest] = []
        for article_path, title, content in pending:
            prompt = CONCEPT_EXTRACTION_PROMPT.format(article_content=content[:MAX_ARTICLE_CHARS])
            inline_requests.append(
                genai_types.InlinedRequest(
                    contents=prompt,
                    # La clé est stockée dans metadata pour retrouver l'article
                    metadata={"key": article_path.stem},
                )
            )

        # 3. Soumettre le batch job
        api_key = self._settings.get_gemini_api_key()
        client = genai.Client(api_key=api_key)

        logger.info(f"Soumission batch job ({len(inline_requests)} requêtes)...")
        batch_job = client.batches.create(
            model=self.model_name,
            src=inline_requests,
            config={"display_name": f"wiki-compile-{source}-{int(time.time())}"},
        )
        job_name = batch_job.name
        logger.info(f"Batch job soumis : {job_name}")
        logger.info(f"État initial : {batch_job.state.name}")

        # 4. Poller jusqu'à completion
        logger.info(f"Polling toutes les {BATCH_POLL_INTERVAL_S}s...")
        while batch_job.state.name not in BATCH_COMPLETED_STATES:
            time.sleep(BATCH_POLL_INTERVAL_S)
            batch_job = client.batches.get(name=job_name)
            logger.info(f"  État : {batch_job.state.name}")

        if batch_job.state.name != "JOB_STATE_SUCCEEDED":
            error_msg = str(batch_job.error) if batch_job.error else "raison inconnue"
            raise RuntimeError(f"Batch job échoué ({batch_job.state.name}) : {error_msg}")

        logger.info("Batch job complété avec succès. Traitement des résultats...")

        # 5. Récupérer et traiter les résultats
        # Construire un index stem → (path, title) pour retrouver les articles
        article_index: dict[str, tuple[Path, str]] = {
            path.stem: (path, title) for path, title, _ in pending
        }

        batch_result = BatchCompilationResult()

        # Récupérer les réponses inline
        responses = batch_job.dest.inlined_responses if batch_job.dest else []
        if not responses:
            logger.warning("Aucune réponse inline trouvée dans le batch job.")
            return batch_result

        for i, inline_response in enumerate(responses):
            # La clé est dans metadata (nouveau format API)
            key = str(i)
            if hasattr(inline_response, "metadata") and inline_response.metadata:
                key = inline_response.metadata.get("key", str(i))
            article_path, article_title = article_index.get(key, (Path(f"unknown/{key}"), key))
            result = CompilationResult(article_path=article_path, article_title=article_title)

            # Vérifier les erreurs
            if hasattr(inline_response, "error") and inline_response.error:
                result.errors.append(f"Batch API error : {inline_response.error}")
                batch_result.results.append(result)
                continue

            # Extraire le texte de la réponse
            try:
                raw_text = None
                if hasattr(inline_response, "response") and inline_response.response:
                    resp = inline_response.response
                    # Naviguer dans candidates → content → parts → text
                    if hasattr(resp, "candidates") and resp.candidates:
                        cand = resp.candidates[0]
                        if hasattr(cand, "content") and cand.content:
                            parts = cand.content.parts or []
                            if parts and hasattr(parts[0], "text"):
                                raw_text = parts[0].text
                    # Fallback : attribut .text direct
                    if raw_text is None and hasattr(resp, "text"):
                        raw_text = resp.text
                if not raw_text:
                    result.errors.append("Réponse vide du batch API")
                    batch_result.results.append(result)
                    continue
            except (AttributeError, IndexError) as e:
                result.errors.append(f"Format de réponse inattendu : {e}")
                batch_result.results.append(result)
                continue

            # Parser et traiter (même logique que compile_article)
            self._process_extraction_result(
                raw_text=raw_text,
                raw_path=article_path,
                article_title=article_title,
                result=result,
            )
            batch_result.results.append(result)

        # 6. Persister cache + index + log
        if batch_result.total_compiled > 0:
            if rebuild_index:
                try:
                    self.indexer.build_master_index()
                except Exception as e:
                    logger.error(f"Erreur génération index : {e}")

            self.cache.save()

            try:
                append_log_entry(
                    vault_path=self.vault_path,
                    operation="compile-batch",
                    title=f"Batch API {batch_result.total_compiled} articles",
                    details={
                        "Source": source,
                        "Fiches créées": batch_result.total_concepts_created,
                        "Fiches mises à jour": batch_result.total_concepts_updated,
                        "Erreurs": batch_result.total_errors,
                        "Mode": "batch-api",
                    },
                )
            except OSError as e:
                logger.warning(f"Impossible d'écrire dans log.md : {e}")

        logger.info(batch_result.summary())
        return batch_result

    def poll_batch_job(
        self,
        job_name: str,
        *,
        rebuild_index: bool = True,
    ) -> BatchCompilationResult:
        """Récupère les résultats d'un batch job existant et les traite.

        Utile pour reprendre un job soumis précédemment sans le re-soumettre.

        Args:
            job_name: Nom du batch job (obtenu lors de la soumission).
            rebuild_index: Si True, régénère l'index maître à la fin.

        Returns:
            BatchCompilationResult avec l'agrégat des résultats.

        Raises:
            RuntimeError: Si le job n'est pas dans un état terminal réussi.
        """
        try:
            from google import genai
        except ImportError as e:
            raise RuntimeError("google-genai non installé. Lancez : uv sync") from e

        api_key = self._settings.get_gemini_api_key()
        client = genai.Client(api_key=api_key)

        batch_job = client.batches.get(name=job_name)
        logger.info(f"Batch job {job_name} : état = {batch_job.state.name}")

        if batch_job.state.name not in BATCH_COMPLETED_STATES:
            logger.info(f"Job non terminé, polling...")
            while batch_job.state.name not in BATCH_COMPLETED_STATES:
                time.sleep(BATCH_POLL_INTERVAL_S)
                batch_job = client.batches.get(name=job_name)
                logger.info(f"  État : {batch_job.state.name}")

        if batch_job.state.name != "JOB_STATE_SUCCEEDED":
            error_msg = str(batch_job.error) if batch_job.error else "raison inconnue"
            raise RuntimeError(f"Batch job échoué ({batch_job.state.name}) : {error_msg}")

        logger.info("Batch job complété. Traitement des résultats...")

        # Collecter tous les articles pour l'index
        articles = self._collect_articles("all")
        article_index: dict[str, tuple[Path, str]] = {}
        for article_path in articles:
            try:
                post = frontmatter.load(str(article_path))
                title = str(post.metadata.get("title", article_path.stem))
                article_index[article_path.stem] = (article_path, title)
            except Exception:
                article_index[article_path.stem] = (article_path, article_path.stem)

        batch_result = BatchCompilationResult()
        responses = batch_job.dest.inlined_responses if batch_job.dest else []

        for i, inline_response in enumerate(responses):
            # La clé est dans metadata (nouveau format API)
            key = str(i)
            if hasattr(inline_response, "metadata") and inline_response.metadata:
                key = inline_response.metadata.get("key", str(i))
            article_path, article_title = article_index.get(key, (Path(f"unknown/{key}"), key))
            result = CompilationResult(article_path=article_path, article_title=article_title)

            if hasattr(inline_response, "error") and inline_response.error:
                result.errors.append(f"Batch API error : {inline_response.error}")
                batch_result.results.append(result)
                continue

            try:
                raw_text = None
                if hasattr(inline_response, "response") and inline_response.response:
                    resp = inline_response.response
                    if hasattr(resp, "candidates") and resp.candidates:
                        cand = resp.candidates[0]
                        if hasattr(cand, "content") and cand.content:
                            parts = cand.content.parts or []
                            if parts and hasattr(parts[0], "text"):
                                raw_text = parts[0].text
                    if raw_text is None and hasattr(resp, "text"):
                        raw_text = resp.text
                if not raw_text:
                    result.errors.append("Réponse vide du batch API")
                    batch_result.results.append(result)
                    continue
            except (AttributeError, IndexError) as e:
                result.errors.append(f"Format de réponse inattendu : {e}")
                batch_result.results.append(result)
                continue

            self._process_extraction_result(
                raw_text=raw_text,
                raw_path=article_path,
                article_title=article_title,
                result=result,
            )
            batch_result.results.append(result)

        if batch_result.total_compiled > 0:
            if rebuild_index:
                try:
                    self.indexer.build_master_index()
                except Exception as e:
                    logger.error(f"Erreur génération index : {e}")

            self.cache.save()

            try:
                append_log_entry(
                    vault_path=self.vault_path,
                    operation="compile-batch-poll",
                    title=f"Poll batch {batch_result.total_compiled} articles",
                    details={
                        "Job": job_name,
                        "Fiches créées": batch_result.total_concepts_created,
                        "Fiches mises à jour": batch_result.total_concepts_updated,
                        "Erreurs": batch_result.total_errors,
                    },
                )
            except OSError as e:
                logger.warning(f"Impossible d'écrire dans log.md : {e}")

        logger.info(batch_result.summary())
        return batch_result

    def _process_extraction_result(
        self,
        raw_text: str,
        raw_path: Path,
        article_title: str,
        result: CompilationResult,
    ) -> None:
        """Traite le résultat d'une extraction Gemini (commun aux deux modes).

        Parse la réponse YAML, crée/met à jour les fiches, ajoute les
        backlinks et marque l'article comme compilé.

        Args:
            raw_text: Texte brut retourné par le LLM.
            raw_path: Chemin du fichier article dans 00_RAW/.
            article_title: Titre de l'article.
            result: CompilationResult à remplir.
        """
        knowledge = _parse_gemini_response(raw_text)

        if knowledge.is_empty():
            logger.warning(
                f"Réponse vide/invalide pour {raw_path.name}. Brut (500 chars) : {raw_text[:500]!r}"
            )
            result.errors.append("Aucune connaissance extraite (réponse vide ou invalide)")
            return

        logger.info(
            f"Extrait : {len(knowledge.concepts)} concepts, "
            f"{len(knowledge.people)} personnes, "
            f"{len(knowledge.technologies)} techs, "
            f"{len(knowledge.topics)} topics"
        )

        source_stem = raw_path.stem
        source_title = article_title
        all_concept_names: list[str] = []

        # Traitement des concepts
        for concept_data in knowledge.concepts:
            try:
                _, created = self.concept_manager.create_or_update_concept(
                    concept_data, source_stem, source_title
                )
                if created:
                    result.concepts_created += 1
                else:
                    result.concepts_updated += 1
                all_concept_names.append(concept_data.name)
            except Exception as e:
                logger.error(f"Concept '{concept_data.name}' : {e}")
                result.errors.append(f"Concept '{concept_data.name}' : {e}")

        # Traitement des personnes
        for person_data in knowledge.people:
            try:
                _, created = self.concept_manager.create_or_update_person(
                    person_data, source_stem, source_title
                )
                if created:
                    result.concepts_created += 1
                else:
                    result.concepts_updated += 1
                all_concept_names.append(person_data.name)
            except Exception as e:
                logger.error(f"Personne '{person_data.name}' : {e}")
                result.errors.append(f"Personne '{person_data.name}' : {e}")

        # Traitement des technologies
        for tech_data in knowledge.technologies:
            try:
                _, created = self.concept_manager.create_or_update_technology(
                    tech_data, source_stem, source_title
                )
                if created:
                    result.concepts_created += 1
                else:
                    result.concepts_updated += 1
                all_concept_names.append(tech_data.name)
            except Exception as e:
                logger.error(f"Tech '{tech_data.name}' : {e}")
                result.errors.append(f"Tech '{tech_data.name}' : {e}")

        # Traitement des topics
        for topic_data in knowledge.topics:
            try:
                path, created = self.concept_manager.create_or_update_topic(
                    topic_data, source_stem, source_title
                )
                if created:
                    result.concepts_created += 1
                else:
                    result.concepts_updated += 1
                all_concept_names.append(topic_data.name)
                if topic_data.related and path:
                    self.linker.add_related_concepts(path, topic_data.related)
            except Exception as e:
                logger.error(f"Topic '{topic_data.name}' : {e}")
                result.errors.append(f"Topic '{topic_data.name}' : {e}")

        # Backlinks : article → concepts
        if all_concept_names:
            added = self.linker.add_concepts_to_article(raw_path, all_concept_names)
            result.backlinks_created = added

        # Marquer l'article comme compilé dans le frontmatter
        try:
            post = frontmatter.load(str(raw_path))
            self._mark_compiled(raw_path, post, knowledge)
        except Exception as e:
            logger.warning(f"Impossible de marquer {raw_path.name} comme compilé : {e}")

        # Mettre à jour le cache
        self.cache.set_article_state(
            raw_path,
            wiki_compiled=True,
            concepts=all_concept_names,
        )
        for concept_name in all_concept_names:
            self.cache.add_backlink(concept_name, source_stem)

        logger.info(
            f"✅ {raw_path.name} : "
            f"{result.concepts_created} créés, "
            f"{result.concepts_updated} mis à jour, "
            f"{result.backlinks_created} liens"
        )

    def get_compilation_stats(self) -> dict:
        """Retourne les statistiques de compilation du vault.

        Utilise le cache persistant pour éviter de scanner et parser
        le frontmatter de tous les fichiers RAW (O(1) au lieu de O(n)).

        Returns:
            Dict avec total_raw, total_compiled, total_wiki_fiches,
            pending_count.
        """
        raw_root = self.vault_path / "00_RAW"
        wiki_root = self.vault_path / "02_WIKI"

        # Nombre total d'articles RAW (simple comptage de fichiers, rapide)
        total_raw = len(list(raw_root.rglob("*.md"))) if raw_root.exists() else 0

        # Statistiques depuis le cache (O(1) au lieu de parser chaque fichier)
        cache_stats = self.cache.get_compilation_stats_from_cache()
        total_compiled = cache_stats["total_compiled"]

        # Si le cache est désynchronisé (plus d'articles sur disque que dans le cache),
        # reconstruire l'index articles
        if total_raw > 0 and cache_stats["total_cached"] == 0:
            logger.info("Cache articles vide, reconstruction...")
            self.cache.rebuild_articles_index(raw_root)
            self.cache.save()
            cache_stats = self.cache.get_compilation_stats_from_cache()
            total_compiled = cache_stats["total_compiled"]

        total_wiki = self.cache.get_total_wiki_fiches()
        # Fallback si le cache fiches est vide
        if total_wiki == 0 and wiki_root.exists():
            total_wiki = len(list(wiki_root.rglob("*.md")))

        return {
            "total_raw": total_raw,
            "total_compiled": total_compiled,
            "pending_count": total_raw - total_compiled,
            "total_wiki_fiches": total_wiki,
        }

    def _collect_articles(self, source: str) -> list[Path]:
        """Collecte les articles RAW à compiler selon la source.

        Args:
            source: "medium", "substack" ou "all".

        Returns:
            Liste triée des chemins d'articles markdown.
        """
        raw_root = self.vault_path / "00_RAW" / "articles"
        sources_map: dict[str, list[Path]] = {
            "medium": [raw_root / "medium"],
            "substack": [raw_root / "substack" / "posts", raw_root / "substack" / "newsletters"],
            "all": [raw_root / "medium", raw_root / "substack"],
            "longform": [raw_root / "longform"],
        }

        dirs = sources_map.get(source, [raw_root])
        articles: list[Path] = []
        for d in dirs:
            if d.exists():
                articles.extend(sorted(d.rglob("*.md")))

        return articles

    def _mark_compiled(
        self,
        raw_path: Path,
        post: frontmatter.Post,
        knowledge: ExtractedKnowledge,
    ) -> None:
        """Marque un article comme compilé dans son frontmatter.

        Args:
            raw_path: Chemin de l'article.
            post: Objet frontmatter déjà parsé.
            knowledge: Connaissances extraites (pour les métadonnées).
        """
        from datetime import date

        post.metadata["wiki_compiled"] = True
        post.metadata["wiki_compiled_date"] = date.today().isoformat()
        post.metadata["wiki_concepts_count"] = knowledge.total_items

        try:
            raw_path.write_text(frontmatter.dumps(post), encoding="utf-8")
        except OSError as e:
            logger.warning(f"Impossible de marquer {raw_path.name} comme compilé : {e}")
