"""Fixtures partagées pour les tests du wiki.

Fournit un vault temporaire avec la structure complète
(00_RAW/, 02_WIKI/, etc.) pour les tests unitaires.
"""

import os
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml


# ---------------------------------------------------------------------------
# Vault temporaire
# ---------------------------------------------------------------------------


@pytest.fixture
def vault_path(tmp_path: Path) -> Path:
    """Crée un vault temporaire avec la structure complète.

    Structure :
        tmp_path/
        ├── 00_RAW/
        │   └── articles/
        │       ├── medium/
        │       ├── substack/
        │       │   ├── posts/
        │       │   └── newsletters/
        │       └── web/
        ├── 01_INBOX/
        ├── 02_WIKI/
        │   ├── Concepts/
        │   ├── People/
        │   ├── Technologies/
        │   ├── Topics/
        │   └── Index/
        ├── 03_OUTPUT/
        │   ├── Reports/
        │   ├── Slides/
        │   └── Graphs/
        └── 04_ARCHIVE/
    """
    vault = tmp_path / "vault"
    dirs = [
        vault / "00_RAW" / "articles" / "medium",
        vault / "00_RAW" / "articles" / "substack" / "posts",
        vault / "00_RAW" / "articles" / "substack" / "newsletters",
        vault / "00_RAW" / "articles" / "web",
        vault / "01_INBOX",
        vault / "02_WIKI" / "Concepts",
        vault / "02_WIKI" / "People",
        vault / "02_WIKI" / "Technologies",
        vault / "02_WIKI" / "Topics",
        vault / "02_WIKI" / "Index",
        vault / "03_OUTPUT" / "Reports",
        vault / "03_OUTPUT" / "Slides",
        vault / "03_OUTPUT" / "Graphs",
        vault / "04_ARCHIVE",
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)

    return vault


# ---------------------------------------------------------------------------
# Mock Settings
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_settings(vault_path: Path):
    """Mock les paramètres de configuration pour pointer vers le vault temporaire.

    Patche get_settings() pour retourner un Settings avec vault_path=tmp_path.
    """
    from src.config import Settings, get_settings

    test_settings = Settings(
        vault_path=str(vault_path),
        local_vault_path=str(vault_path),
        gemini_api_key="test-api-key",
        google_api_key="",
        medium_extract_output=str(vault_path / "external" / "medium"),
        substack_extract_output=str(vault_path / "external" / "substack"),
        log_level="WARNING",
    )

    with patch("src.config.get_settings", return_value=test_settings):
        with patch("src.wiki.concept_manager.get_settings", return_value=test_settings):
            with patch("src.wiki.linker.get_settings", return_value=test_settings):
                with patch("src.wiki.indexer.get_settings", return_value=test_settings):
                    yield test_settings


@pytest.fixture
def mock_gemini_key():
    """Fournit une clé API Gemini factice via variable d'environnement."""
    with patch.dict(os.environ, {"GEMINI_API_KEY": "test-api-key-12345"}):
        yield "test-api-key-12345"


# ---------------------------------------------------------------------------
# Helpers — création de fichiers de test
# ---------------------------------------------------------------------------


def make_article(
    vault_path: Path,
    source: str = "medium",
    filename: str = "test-article",
    title: str = "Test Article",
    content: str = "Contenu de l'article de test.",
    extra_metadata: dict | None = None,
) -> Path:
    """Crée un article RAW dans le vault temporaire.

    Args:
        vault_path: Racine du vault temporaire.
        source: Source de l'article (medium, substack, web).
        filename: Nom du fichier sans extension.
        title: Titre de l'article.
        content: Contenu markdown de l'article.
        extra_metadata: Métadonnées supplémentaires pour le frontmatter.

    Returns:
        Chemin du fichier créé.
    """
    dest_dir = vault_path / "00_RAW" / "articles" / source
    if source == "substack":
        dest_dir = dest_dir / "posts"
    dest_dir.mkdir(parents=True, exist_ok=True)

    metadata = {
        "title": title,
        "source": source,
        "date": date.today().isoformat(),
    }
    if extra_metadata:
        metadata.update(extra_metadata)

    frontmatter_str = yaml.dump(metadata, allow_unicode=True, default_flow_style=False).strip()
    file_content = f"---\n{frontmatter_str}\n---\n\n{content}\n"

    file_path = dest_dir / f"{filename}.md"
    file_path.write_text(file_content, encoding="utf-8")
    return file_path


def make_concept(
    vault_path: Path,
    name: str = "Test Concept",
    wiki_type: str = "concept",
    definition: str = "Définition de test.",
    sources: list[str] | None = None,
    related: list[str] | None = None,
    aliases: list[str] | None = None,
    category: str = "",
) -> Path:
    """Crée une fiche wiki dans le vault temporaire.

    Args:
        vault_path: Racine du vault temporaire.
        name: Nom du concept.
        wiki_type: Type de fiche (concept, person, technology, topic).
        definition: Définition du concept.
        sources: Liste des stems d'articles sources.
        related: Liste des noms de concepts liés.
        aliases: Noms alternatifs.
        category: Catégorie optionnelle.

    Returns:
        Chemin du fichier créé.
    """
    from src.wiki.concept_manager import _sanitize_filename, WIKI_TYPE_DIRS

    subdir = WIKI_TYPE_DIRS.get(wiki_type, "Concepts")
    dest_dir = vault_path / "02_WIKI" / subdir
    dest_dir.mkdir(parents=True, exist_ok=True)

    filename = _sanitize_filename(name) + ".md"
    today = date.today().isoformat()

    source_count = len(sources) if sources else 0
    source_links = "\n".join(f"- [[{s}]]" for s in (sources or []))
    related_links = "\n".join(f"- [[{r}]]" for r in (related or []))

    section_labels = {
        "concept": "Définition",
        "person": "Biographie",
        "technology": "Description",
        "topic": "Vue d'ensemble",
    }
    section_label = section_labels.get(wiki_type, "Description")

    metadata = {
        "title": name,
        "type": wiki_type,
        "aliases": aliases or [],
        "created": today,
        "updated": today,
        "source_count": source_count,
    }
    if category:
        metadata["category"] = category

    frontmatter_str = yaml.dump(metadata, allow_unicode=True, default_flow_style=False).strip()

    content = f"""---
{frontmatter_str}
---

# {name}

## {section_label}

{definition}

## Sources

{source_links or "_Aucune source._"}

## Concepts liés

{related_links or "_À compléter_"}

## Questions ouvertes

_À compléter_
"""

    file_path = dest_dir / filename
    file_path.write_text(content, encoding="utf-8")
    return file_path
