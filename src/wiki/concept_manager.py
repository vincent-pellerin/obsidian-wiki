"""Gestionnaire des fiches concepts du wiki.

Gère le CRUD des fichiers markdown dans 02_WIKI/{Concepts,People,Technologies,Topics}.
"""

import logging
import re
from datetime import date
from pathlib import Path

import frontmatter
import yaml

from src.config import get_settings
from src.wiki.models import ConceptData, PersonData, TechData, TopicData

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
        Nom de fichier sûr sans caractères spéciaux.

    Example:
        >>> _sanitize_filename("Graph RAG / Knowledge")
        'Graph_RAG_Knowledge'
    """
    # Remplace les caractères non alphanumériques (sauf tirets) par _
    safe = re.sub(r"[^\w\s\-]", "", name, flags=re.UNICODE)
    safe = re.sub(r"[\s]+", "_", safe.strip())
    return safe


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

_À compléter_

## Questions ouvertes

_À compléter_
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

    Attributes:
        wiki_root: Chemin vers 02_WIKI/ dans le vault.
    """

    def __init__(self) -> None:
        """Initialise avec la configuration courante."""
        settings = get_settings()
        self.wiki_root = Path(settings.get_vault_path()) / "02_WIKI"

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
        )

    def create_or_update_person(
        self,
        data: PersonData,
        source_stem: str,
        source_title: str = "",
    ) -> tuple[Path, bool]:
        """Crée ou met à jour la fiche d'une personne.

        Args:
            data: Données de la personne extraites par le LLM.
            source_stem: Nom du fichier source sans extension.
            source_title: Titre lisible de l'article source.

        Returns:
            Tuple (chemin_fichier, created) où created=True si nouvelle fiche.
        """
        return self._upsert(
            name=data.name,
            wiki_type="person",
            definition=data.role,
            context=data.context,
            aliases=[],
            source_stem=source_stem,
            source_title=source_title,
        )

    def create_or_update_technology(
        self,
        data: TechData,
        source_stem: str,
        source_title: str = "",
    ) -> tuple[Path, bool]:
        """Crée ou met à jour la fiche d'une technologie.

        Args:
            data: Données de la technologie extraites par le LLM.
            source_stem: Nom du fichier source sans extension.
            source_title: Titre lisible de l'article source.

        Returns:
            Tuple (chemin_fichier, created) où created=True si nouvelle fiche.
        """
        return self._upsert(
            name=data.name,
            wiki_type="technology",
            definition=f"**Type** : {data.type}\n\n{data.context}",
            context=data.context,
            aliases=[],
            source_stem=source_stem,
            source_title=source_title,
            category=data.type,
        )

    def create_or_update_topic(
        self,
        data: TopicData,
        source_stem: str,
        source_title: str = "",
    ) -> tuple[Path, bool]:
        """Crée ou met à jour la fiche d'un topic.

        Args:
            data: Données du topic extraites par le LLM.
            source_stem: Nom du fichier source sans extension.
            source_title: Titre lisible de l'article source.

        Returns:
            Tuple (chemin_fichier, created) où created=True si nouvelle fiche.
        """
        related_str = ", ".join(data.related) if data.related else ""
        return self._upsert(
            name=data.name,
            wiki_type="topic",
            definition=f"Sujet : {data.name}",
            context=f"Sujets liés : {related_str}" if related_str else "",
            aliases=[],
            source_stem=source_stem,
            source_title=source_title,
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

        Returns:
            Tuple (chemin_fichier, created).
        """
        target_dir = _wiki_dir_for_type(self.wiki_root, wiki_type)
        target_dir.mkdir(parents=True, exist_ok=True)

        filename = _sanitize_filename(name) + ".md"
        file_path = target_dir / filename

        if not file_path.exists():
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
            )
            file_path.write_text(content, encoding="utf-8")
            logger.info(f"Fiche créée : {file_path.relative_to(self.wiki_root)}")
            return file_path, True

        # Mise à jour : ajouter la source si pas déjà présente
        updated = self._add_source_to_existing(file_path, source_stem, source_title)
        if updated:
            logger.info(f"Fiche mise à jour : {file_path.name} (+source {source_stem})")
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
