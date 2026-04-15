"""Gestionnaire des fiches concepts du wiki.

Gère le CRUD des fichiers markdown dans 02_WIKI/{Concepts,People,Technologies,Topics}.
"""

from __future__ import annotations

import logging
import re
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

import frontmatter
import yaml

from src.config import get_settings
from src.wiki.models import ConceptData, PersonData, TechData, TopicData

if TYPE_CHECKING:
    from src.wiki.cache import WikiStateCache

logger = logging.getLogger(__name__)

# Mapping type de fiche → sous-dossier dans 02_WIKI/
WIKI_TYPE_DIRS: dict[str, str] = {
    "concept": "Concepts",
    "person": "People",
    "technology": "Technologies",
    "topic": "Topics",
}


def _sanitize_filename(name: str) -> str:
    """Convertit un nom en nom de fichier valide.

    Args:
        name: Nom du concept (ex: "GraphRAG", "John Doe").

    Returns:
        Nom de fichier sûr sans caractères spéciaux, normalisé en PascalCase.

    Example:
        >>> _sanitize_filename("Graph RAG / Knowledge")
        'Graph_RAG_Knowledge'
        >>> _sanitize_filename("AI inflation")
        'AI_Inflation'
    """
    # Remplace les caractères non alphanumériques (sauf tirets) par _
    safe = re.sub(r"[^\w\s\-]", "", name, flags=re.UNICODE)
    safe = re.sub(r"[\s]+", "_", safe.strip())

    # Normaliser la casse : PascalCase (première lettre de chaque mot en majuscule)
    # Gérer les acronymes comme AI, API, etc. en les gardant en majuscules
    words = safe.split("_")
    normalized_words = []
    for word in words:
        if len(word) <= 2 and word.isupper():
            # Garder les acronymes courts en majuscules (AI, API, IP, etc.)
            normalized_words.append(word)
        else:
            # PascalCase pour les autres mots
            normalized_words.append(word.capitalize())

    return "_".join(normalized_words)


def _wiki_dir_for_type(wiki_root: Path, wiki_type: str) -> Path:
    """Retourne le répertoire wiki pour un type de fiche.

    Args:
        wiki_root: Racine du répertoire 02_WIKI/.
        wiki_type: Type de fiche (concept|person|technology|topic).

    Returns:
        Chemin vers le sous-dossier correspondant.

    Raises:
        ValueError: Si le type de fiche est inconnu.
    """
    subdir = WIKI_TYPE_DIRS.get(wiki_type)
    if subdir is None:
        raise ValueError(f"Type de fiche inconnu : {wiki_type}. Valides : {list(WIKI_TYPE_DIRS)}")
    return wiki_root / subdir


def _build_concept_content(
    name: str,
    wiki_type: str,
    definition: str,
    context: str,
    aliases: list[str],
    source_stem: str,
    source_title: str,
    category: str = "",
    related: list[str] | None = None,
    questions: list[str] | None = None,
) -> str:
    """Construit le contenu Markdown d'une nouvelle fiche concept.

    Args:
        name: Nom du concept.
        wiki_type: Type (concept|person|technology|topic).
        definition: Définition du concept.
        context: Contexte d'usage dans l'article source.
        aliases: Noms alternatifs.
        source_stem: Identifiant de l'article source (nom du fichier sans .md).
        source_title: Titre lisible de l'article source.
        category: Catégorie thématique (optionnel).
        related: Concepts liés identifiés par le LLM.
        questions: Questions ouvertes sur ce concept.

    Returns:
        Contenu Markdown complet de la fiche.
    """
    today = date.today().isoformat()
    meta: dict = {
        "title": name,
        "type": wiki_type,
        "aliases": aliases,
        "created": today,
        "updated": today,
        "source_count": 1,
    }
    if category:
        meta["category"] = category

    frontmatter_str = yaml.dump(meta, allow_unicode=True, default_flow_style=False).strip()

    section_label = _section_label_for_type(wiki_type)
    source_link = (
        f"- [[{source_stem}]] — {source_title}" if source_title else f"- [[{source_stem}]]"
    )

    # Section "Concepts liés"
    if related:
        related_lines = "\n".join(f"- [[{r}]]" for r in related)
    else:
        related_lines = "_À compléter_"

    # Section "Questions ouvertes"
    if questions:
        questions_lines = "\n".join(f"- {q}" for q in questions)
    else:
        questions_lines = "_À compléter_"

    content = f"""---
{frontmatter_str}
---

# {name}

## {section_label}

{definition}

## Contexte

{context or "_Aucun contexte disponible._"}

## Sources

{source_link}

## Concepts liés

{related_lines}

## Questions ouvertes

{questions_lines}
"""
    return content


def _section_label_for_type(wiki_type: str) -> str:
    """Retourne le label de la section principale selon le type.

    Args:
        wiki_type: Type de fiche.

    Returns:
        Label de la section (ex: "Définition", "Biographie").
    """
    labels = {
        "concept": "Définition",
        "person": "Biographie",
        "technology": "Description",
        "topic": "Vue d'ensemble",
    }
    return labels.get(wiki_type, "Description")


class ConceptManager:
    """Gère le CRUD des fiches dans 02_WIKI/.

    Crée ou met à jour les fichiers markdown des concepts, personnes,
    technologies et topics dans les sous-dossiers appropriés du vault.

    Maintient un index en mémoire (stem → Path, title → Path) pour
    des lookups O(1) au lieu de scans répétés du répertoire.

    Attributes:
        wiki_root: Chemin vers 02_WIKI/ dans le vault.
        cache: Cache persistant partagé (optionnel).
    """

    def __init__(self, cache: WikiStateCache | None = None) -> None:
        """Initialise avec la configuration courante et un cache optionnel.

        Args:
            cache: Instance de WikiStateCache partagée pour les lookups rapides.
        """
        settings = get_settings()
        self.wiki_root = Path(settings.get_vault_path()) / "02_WIKI"
        self._cache = cache
        self._stem_index: dict[str, Path] = {}
        self._title_index: dict[str, Path] = {}
        self._build_memory_index()

    def _build_memory_index(self) -> None:
        """Construit l'index en mémoire stem→Path et title→Path.

        Scanne le répertoire wiki une seule fois au démarrage pour
        indexer tous les fichiers. Les lookups suivants sont en O(1).
        """
        if not self.wiki_root.exists():
            return

        for md_file in self.wiki_root.rglob("*.md"):
            if md_file.stem.startswith("000_"):
                continue
            stem_lower = md_file.stem.lower()
            self._stem_index[stem_lower] = md_file
            # Aussi indexer par titre si disponible dans le cache
            if self._cache:
                state = self._cache.get_fiche_state(md_file.stem)
                if state and state.get("title"):
                    self._title_index[state["title"].lower()] = md_file

    def find_fiche_by_name(self, name: str) -> Path | None:
        """Recherche une fiche par nom (stem ou titre) en O(1).

        Args:
            name: Nom du concept, stem du fichier ou titre.

        Returns:
            Chemin de la fiche trouvée, ou None si introuvable.
        """
        # Recherche par stem (exact, case-insensitive)
        name_lower = name.lower()
        result = self._stem_index.get(name_lower)
        if result and result.exists():
            return result

        # Recherche par stem sanitisé
        sanitized_lower = _sanitize_filename(name).lower()
        result = self._stem_index.get(sanitized_lower)
        if result and result.exists():
            return result

        # Recherche par titre
        result = self._title_index.get(name_lower)
        if result and result.exists():
            return result

        return None

    def _register_in_index(self, file_path: Path, title: str = "") -> None:
        """Enregistre une fiche dans l'index en mémoire.

        Args:
            file_path: Chemin de la fiche à indexer.
            title: Titre de la fiche (optionnel).
        """
        self._stem_index[file_path.stem.lower()] = file_path
        if title:
            self._title_index[title.lower()] = file_path

    def create_or_update_concept(
        self,
        data: ConceptData,
        source_stem: str,
        source_title: str = "",
    ) -> tuple[Path, bool]:
        """Crée ou met à jour la fiche d'un concept.

        Args:
            data: Données du concept extraites par le LLM.
            source_stem: Nom du fichier source sans extension.
            source_title: Titre lisible de l'article source.

        Returns:
            Tuple (chemin_fichier, created) où created=True si nouvelle fiche.
        """
        return self._upsert(
            name=data.name,
            wiki_type="concept",
            definition=data.definition,
            context=data.context,
            aliases=data.aliases,
            source_stem=source_stem,
            source_title=source_title,
            related=data.related,
            questions=data.questions,
        )

    def create_or_update_person(
        self,
        data: PersonData,
        source_stem: str,
        source_title: str = "",
    ) -> tuple[Path, bool]:
        """Crée ou met à jour la fiche d'une personne.

        La biographie (data.bio) est utilisée comme contenu principal.
        Le rôle est ajouté en complément si la bio est absente.

        Args:
            data: Données de la personne extraites par le LLM.
            source_stem: Nom du fichier source sans extension.
            source_title: Titre lisible de l'article source.

        Returns:
            Tuple (chemin_fichier, created) où created=True si nouvelle fiche.
        """
        # Utiliser la bio comme définition principale ; fallback sur le rôle
        definition = data.bio if data.bio else data.role
        return self._upsert(
            name=data.name,
            wiki_type="person",
            definition=definition,
            context=data.context,
            aliases=[],
            source_stem=source_stem,
            source_title=source_title,
            related=data.related,
        )

    def create_or_update_technology(
        self,
        data: TechData,
        source_stem: str,
        source_title: str = "",
    ) -> tuple[Path, bool]:
        """Crée ou met à jour la fiche d'une technologie.

        La description autonome (data.description) est utilisée comme définition.
        Le type est stocké dans le frontmatter (category), pas dans la définition.

        Args:
            data: Données de la technologie extraites par le LLM.
            source_stem: Nom du fichier source sans extension.
            source_title: Titre lisible de l'article source.

        Returns:
            Tuple (chemin_fichier, created) où created=True si nouvelle fiche.
        """
        # Utiliser la description autonome ; fallback sur le contexte si absente
        definition = data.description if data.description else data.context
        return self._upsert(
            name=data.name,
            wiki_type="technology",
            definition=definition,
            context=data.context,
            aliases=[],
            source_stem=source_stem,
            source_title=source_title,
            category=data.type,
            related=data.related,
            questions=data.questions,
        )

    def create_or_update_topic(
        self,
        data: TopicData,
        source_stem: str,
        source_title: str = "",
    ) -> tuple[Path, bool]:
        """Crée ou met à jour la fiche d'un topic.

        La définition du sujet (data.definition) est utilisée comme contenu principal.
        Les sujets liés sont passés comme related pour peupler la section "Concepts liés".

        Args:
            data: Données du topic extraites par le LLM.
            source_stem: Nom du fichier source sans extension.
            source_title: Titre lisible de l'article source.

        Returns:
            Tuple (chemin_fichier, created) où created=True si nouvelle fiche.
        """
        return self._upsert(
            name=data.name,
            wiki_type="topic",
            definition=data.definition or f"Sujet : {data.name}",
            context="",
            aliases=[],
            source_stem=source_stem,
            source_title=source_title,
            related=data.related,
        )

    def get_concept_path(self, name: str, wiki_type: str = "concept") -> Path | None:
        """Retourne le chemin d'une fiche si elle existe.

        Args:
            name: Nom du concept.
            wiki_type: Type de fiche.

        Returns:
            Chemin du fichier ou None s'il n'existe pas.
        """
        target_dir = _wiki_dir_for_type(self.wiki_root, wiki_type)
        filename = _sanitize_filename(name) + ".md"
        path = target_dir / filename
        return path if path.exists() else None

    def list_all(self, wiki_type: str | None = None) -> list[Path]:
        """Liste toutes les fiches du wiki.

        Args:
            wiki_type: Si spécifié, filtre par type. Sinon retourne tout.

        Returns:
            Liste des chemins de fiches markdown.
        """
        if wiki_type:
            target_dir = _wiki_dir_for_type(self.wiki_root, wiki_type)
            if not target_dir.exists():
                return []
            return list(target_dir.glob("*.md"))

        results: list[Path] = []
        for subdir in WIKI_TYPE_DIRS.values():
            d = self.wiki_root / subdir
            if d.exists():
                results.extend(d.glob("*.md"))
        return results

    def _upsert(
        self,
        name: str,
        wiki_type: str,
        definition: str,
        context: str,
        aliases: list[str],
        source_stem: str,
        source_title: str,
        category: str = "",
        related: list[str] | None = None,
        questions: list[str] | None = None,
    ) -> tuple[Path, bool]:
        """Crée ou met à jour une fiche wiki.

        Args:
            name: Nom de l'entité.
            wiki_type: Type de fiche.
            definition: Définition ou description.
            context: Contexte d'usage.
            aliases: Noms alternatifs.
            source_stem: Identifiant de l'article source.
            source_title: Titre de l'article source.
            category: Catégorie optionnelle.
            related: Concepts liés (pour la section "Concepts liés").
            questions: Questions ouvertes (pour la section "Questions ouvertes").

        Returns:
            Tuple (chemin_fichier, created).
        """
        target_dir = _wiki_dir_for_type(self.wiki_root, wiki_type)
        target_dir.mkdir(parents=True, exist_ok=True)

        filename = _sanitize_filename(name) + ".md"
        file_path = target_dir / filename

        if not file_path.exists():
            # Déduplication cross-catégorie : vérifier si le concept existe déjà
            # dans une autre catégorie avant de créer un doublon
            existing = self.find_fiche_by_name(name)
            if existing and existing != file_path:
                logger.debug(
                    f"Déduplication : '{name}' existe déjà dans {existing.parent.name}, "
                    f"skip création dans {target_dir.name}"
                )
                updated = self._add_source_to_existing(existing, source_stem, source_title)
                if updated and self._cache:
                    state = self._cache.get_fiche_state(existing.stem)
                    current_count = state["source_count"] if state else 1
                    self._cache.set_fiche_state(
                        existing,
                        wiki_type=wiki_type,
                        source_count=current_count + 1,
                        title=name,
                    )
                return existing, False

            # Création
            content = _build_concept_content(
                name=name,
                wiki_type=wiki_type,
                definition=definition,
                context=context,
                aliases=aliases,
                source_stem=source_stem,
                source_title=source_title,
                category=category,
                related=related,
                questions=questions,
            )
            file_path.write_text(content, encoding="utf-8")
            logger.info(f"Fiche créée : {file_path.relative_to(self.wiki_root)}")

            # Mettre à jour les index
            self._register_in_index(file_path, title=name)
            if self._cache:
                self._cache.set_fiche_state(
                    file_path,
                    wiki_type=wiki_type,
                    source_count=1,
                    title=name,
                )
            return file_path, True

        # Mise à jour : ajouter la source si pas déjà présente
        updated = self._add_source_to_existing(file_path, source_stem, source_title)
        if updated:
            logger.info(f"Fiche mise à jour : {file_path.name} (+source {source_stem})")
            # Mettre à jour le cache avec le nouveau source_count
            if self._cache:
                state = self._cache.get_fiche_state(file_path.stem)
                current_count = state["source_count"] if state else 1
                self._cache.set_fiche_state(
                    file_path,
                    wiki_type=wiki_type,
                    source_count=current_count + 1,
                    title=name,
                )
        else:
            logger.debug(f"Fiche déjà à jour : {file_path.name}")
        return file_path, False

    def _add_source_to_existing(
        self,
        file_path: Path,
        source_stem: str,
        source_title: str,
    ) -> bool:
        """Ajoute une source dans la section ## Sources d'une fiche existante.

        Args:
            file_path: Chemin de la fiche à mettre à jour.
            source_stem: Identifiant de l'article source.
            source_title: Titre de l'article source.

        Returns:
            True si la fiche a été modifiée, False si déjà à jour.
        """
        content = file_path.read_text(encoding="utf-8")

        # Vérifier si la source est déjà présente
        if f"[[{source_stem}]]" in content:
            return False

        # Ajouter la source dans la section ## Sources
        source_link = (
            f"- [[{source_stem}]] — {source_title}" if source_title else f"- [[{source_stem}]]"
        )

        # Chercher la section Sources et ajouter après la dernière entrée
        sources_pattern = re.compile(r"(## Sources\n)(.*?)(\n## |\Z)", re.DOTALL)
        match = sources_pattern.search(content)

        if match:
            existing_sources = match.group(2).rstrip()
            new_sources = (
                existing_sources + f"\n{source_link}" if existing_sources.strip() else source_link
            )
            content = content[: match.start(2)] + new_sources + content[match.end(2) :]
        else:
            # Section Sources absente : l'ajouter en fin de fichier
            content = content.rstrip() + f"\n\n## Sources\n\n{source_link}\n"

        # Mettre à jour la date et le compteur dans le frontmatter
        try:
            post = frontmatter.loads(content)
            post.metadata["updated"] = date.today().isoformat()
            post.metadata["source_count"] = post.metadata.get("source_count", 1) + 1
            content = frontmatter.dumps(post)
        except Exception as e:
            logger.warning(f"Impossible de mettre à jour le frontmatter de {file_path.name}: {e}")

        file_path.write_text(content, encoding="utf-8")
        return True
