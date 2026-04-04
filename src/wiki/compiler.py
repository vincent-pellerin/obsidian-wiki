"""Compilateur wiki — transforme les articles bruts en fiches concepts.

Orchestre le pipeline complet pour chaque article :
  1. Lecture du fichier RAW (markdown + frontmatter)
  2. Extraction des concepts via Gemini LLM
  3. Création/mise à jour des fiches dans 02_WIKI/
  4. Ajout des backlinks bidirectionnels
  5. Mise à jour de l'index maître
"""

import logging
import re
import time
from pathlib import Path

import frontmatter
import yaml

from src.config import get_settings
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
MAX_ARTICLE_CHARS = 12_000

# Nombre max de tentatives pour l'appel Gemini
MAX_RETRIES = 3
RETRY_DELAY_S = 5.0

CONCEPT_EXTRACTION_PROMPT = """\
Analyse cet article et identifie les éléments suivants.
Retourne UNIQUEMENT du YAML valide, sans balises markdown, sans commentaires.

Format attendu :
concepts:
  - name: "Nom du concept"
    definition: "Définition concise (1-2 phrases)"
    context: "Comment ce concept est utilisé dans l'article"
    aliases: []
people:
  - name: "Prénom Nom"
    role: "Rôle ou titre"
    context: "Contexte de mention dans l'article"
technologies:
  - name: "Nom outil/techno"
    type: "database|framework|library|platform|language|tool"
    context: "Comment cet outil est utilisé dans l'article"
topics:
  - name: "Sujet principal"
    related:
      - "sujet lié 1"
      - "sujet lié 2"

Règles :
- 5 à 10 concepts clés maximum
- Uniquement les éléments réellement présents dans l'article
- Définitions en français
- Si une catégorie est vide, mettre une liste vide []
- Noms propres en anglais si c'est la langue d'origine

Article :
{article_content}
"""


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
            )
        )

    # People
    for item in data.get("people") or []:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        knowledge.people.append(
            PersonData(
                name=str(item["name"]),
                role=str(item.get("role", "")),
                context=str(item.get("context", "")),
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
                context=str(item.get("context", "")),
            )
        )

    # Topics
    for item in data.get("topics") or []:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        knowledge.topics.append(
            TopicData(
                name=str(item["name"]),
                related=list(item.get("related") or []),
            )
        )

    return knowledge


def _call_gemini(content: str, model_name: str, api_key: str) -> str:
    """Appelle l'API Gemini pour extraire les concepts d'un article.

    Args:
        content: Contenu de l'article à analyser.
        model_name: Nom du modèle Gemini.
        api_key: Clé API Gemini.

    Returns:
        Texte brut de la réponse Gemini.

    Raises:
        RuntimeError: Si toutes les tentatives ont échoué.
    """
    try:
        import google.generativeai as genai
    except ImportError as e:
        raise RuntimeError("google-generativeai non installé. Lancez : uv sync") from e

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(model_name)

    prompt = CONCEPT_EXTRACTION_PROMPT.format(article_content=content[:MAX_ARTICLE_CHARS])

    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = model.generate_content(prompt)
            return response.text
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


class WikiCompiler:
    """Compile les articles RAW en fiches wiki structurées.

    Orchestre le pipeline complet : extraction LLM → ConceptManager
    → Linker → Indexer.

    Attributes:
        concept_manager: Gestion CRUD des fiches wiki.
        linker: Gestion des backlinks.
        indexer: Génération de l'index maître.
        vault_path: Chemin racine du vault.
    """

    def __init__(self) -> None:
        """Initialise le compilateur avec la configuration courante."""
        settings = get_settings()
        self.concept_manager = ConceptManager()
        self.linker = Linker()
        self.indexer = Indexer()
        self.vault_path = Path(settings.vault_path)
        self._settings = settings

    def compile_article(
        self,
        raw_path: Path,
        *,
        force: bool = False,
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

        logger.info(f"Compilation : {raw_path.name} ({len(article_content)} chars)")

        try:
            raw_response = _call_gemini(
                content=article_content,
                model_name=self._settings.gemini_model_wiki,
                api_key=self._settings.gemini_api_key,
            )
        except RuntimeError as e:
            result.errors.append(f"Gemini : {e}")
            return result

        knowledge = _parse_gemini_response(raw_response)

        if knowledge.is_empty():
            result.errors.append("Aucune connaissance extraite (réponse Gemini vide ou invalide)")
            return result

        logger.info(
            f"Extrait : {len(knowledge.concepts)} concepts, "
            f"{len(knowledge.people)} personnes, "
            f"{len(knowledge.technologies)} techs, "
            f"{len(knowledge.topics)} topics"
        )

        source_stem = raw_path.stem
        source_title = result.article_title
        all_concept_names: list[str] = []

        # Traitement des concepts
        for concept_data in knowledge.concepts:
            try:
                path, created = self.concept_manager.create_or_update_concept(
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
                path, created = self.concept_manager.create_or_update_person(
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
                path, created = self.concept_manager.create_or_update_technology(
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
                # Lier les topics entre eux (related)
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
        self._mark_compiled(raw_path, post, knowledge)

        logger.info(
            f"✅ {raw_path.name} : "
            f"{result.concepts_created} créés, "
            f"{result.concepts_updated} mis à jour, "
            f"{result.backlinks_created} liens"
        )
        return result

    def batch_compile(
        self,
        source: str = "all",
        limit: int | None = None,
        *,
        force: bool = False,
        rebuild_index: bool = True,
    ) -> BatchCompilationResult:
        """Compile tous les articles RAW d'une source donnée.

        Args:
            source: Source à compiler ("medium", "substack", ou "all").
            limit: Nombre maximum d'articles à traiter.
            force: Si True, recompile les articles déjà compilés.
            rebuild_index: Si True, régénère l'index maître à la fin.

        Returns:
            BatchCompilationResult avec l'agrégat des résultats.
        """
        articles = self._collect_articles(source)
        if limit:
            articles = articles[:limit]

        logger.info(f"Batch compile : {len(articles)} articles (source={source})")

        batch = BatchCompilationResult()
        for i, article_path in enumerate(articles, 1):
            logger.info(f"[{i}/{len(articles)}] {article_path.name}")
            result = self.compile_article(article_path, force=force)
            batch.results.append(result)

        if rebuild_index and batch.total_compiled > 0:
            try:
                self.indexer.build_master_index()
            except Exception as e:
                logger.error(f"Erreur génération index : {e}")

        logger.info(batch.summary())
        return batch

    def get_compilation_stats(self) -> dict:
        """Retourne les statistiques de compilation du vault.

        Returns:
            Dict avec total_raw, total_compiled, total_wiki_fiches,
            pending_count.
        """
        raw_root = self.vault_path / "00_RAW"
        wiki_root = self.vault_path / "02_WIKI"

        total_raw = len(list(raw_root.rglob("*.md"))) if raw_root.exists() else 0

        # Compter les compilés (frontmatter wiki_compiled: true)
        total_compiled = 0
        for md in raw_root.rglob("*.md") if raw_root.exists() else []:
            try:
                post = frontmatter.load(str(md))
                if post.metadata.get("wiki_compiled"):
                    total_compiled += 1
            except Exception:
                pass

        total_wiki = len(list(wiki_root.rglob("*.md"))) if wiki_root.exists() else 0

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
