"""Tests unitaires pour l'enricher.

Couvre :
- Enricher.suggest_missing_connections : connexions manquantes
- Enricher.enrich_concept : enrichissement via Gemini (mock)
- Enricher._load_sources_content : chargement des sources
- Enricher._find_concept_file : recherche de fiche
- Enricher._find_raw_file : recherche de fichier RAW
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from src.lint.enricher import MAX_SOURCE_CHARS, Enricher
from tests.conftest import make_concept, make_article


def _make_concept_with_frontmatter_sources(
    vault_path: Path,
    name: str,
    sources: list[str],
    related: list[str] | None = None,
) -> Path:
    """Crée une fiche wiki avec les sources dans le frontmatter YAML.

    L'enricher lit les sources depuis post.metadata.get('sources'),
    pas depuis le corps markdown. Cette helper crée le bon format.
    """
    concepts_dir = vault_path / "02_WIKI" / "Concepts"
    concepts_dir.mkdir(parents=True, exist_ok=True)

    from src.wiki.concept_manager import _sanitize_filename

    filename = _sanitize_filename(name) + ".md"

    sources_yaml = "\n".join(f"  - {s}" for s in sources)
    related_links = "\n".join(f"- [[{r}]]" for r in (related or []))

    content = f"""---
title: "{name}"
type: concept
source_count: {len(sources)}
sources:
{sources_yaml}
---

# {name}

## Définition

Definition of {name}.

## Concepts liés

{related_links or "_À compléter_"}
"""
    path = concepts_dir / filename
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Enricher.suggest_missing_connections
# ---------------------------------------------------------------------------


class TestSuggestMissingConnections:
    """Tests pour Enricher.suggest_missing_connections."""

    @pytest.fixture
    def enricher(self, vault_path, mock_settings):
        with patch("src.lint.enricher.get_settings", return_value=mock_settings):
            with patch("src.lint.enricher.ConceptManager"):
                return Enricher()

    def test_suggests_connection_between_shared_sources(self, enricher, vault_path):
        """Deux fiches partageant ≥2 sources non liées → suggestion."""
        _make_concept_with_frontmatter_sources(vault_path, "RAG", ["article-1", "article-2"])
        _make_concept_with_frontmatter_sources(vault_path, "GraphRAG", ["article-1", "article-2"])

        suggestions = enricher.suggest_missing_connections()
        assert len(suggestions) >= 1

    def test_no_suggestion_for_already_linked(self, enricher, vault_path):
        """Fiches déjà liées → pas de suggestion."""
        _make_concept_with_frontmatter_sources(
            vault_path, "RAG", ["article-1", "article-2"], related=["Graphrag"]
        )
        _make_concept_with_frontmatter_sources(vault_path, "GraphRAG", ["article-1", "article-2"])

        suggestions = enricher.suggest_missing_connections()
        assert len(suggestions) == 0

    def test_no_suggestion_for_single_shared_source(self, enricher, vault_path):
        """Fiches partageant seulement 1 source → pas de suggestion."""
        _make_concept_with_frontmatter_sources(vault_path, "RAG", ["article-1"])
        _make_concept_with_frontmatter_sources(vault_path, "GraphRAG", ["article-1"])

        suggestions = enricher.suggest_missing_connections()
        assert len(suggestions) == 0

    def test_empty_wiki_no_suggestions(self, enricher, vault_path):
        suggestions = enricher.suggest_missing_connections()
        assert suggestions == []


# ---------------------------------------------------------------------------
# Enricher.enrich_concept (mock Gemini)
# ---------------------------------------------------------------------------


class TestEnrichConcept:
    """Tests pour Enricher.enrich_concept avec Gemini mocké."""

    @pytest.fixture
    def enricher(self, vault_path, mock_settings):
        with patch("src.lint.enricher.get_settings", return_value=mock_settings):
            with patch("src.lint.enricher.ConceptManager") as mock_cm_class:
                mock_cm = MagicMock()
                mock_cm_class.return_value = mock_cm
                return Enricher()

    def test_enrich_concept_not_found(self, enricher, vault_path):
        """Concept inexistant → retourne False."""
        enricher._concept_manager.find_fiche_by_name.return_value = None
        result = enricher.enrich_concept("NonExistent")
        assert result is False

    def test_enrich_concept_success(self, enricher, vault_path):
        """Enrichissement réussi avec Gemini mocké."""
        concept_path = make_concept(vault_path, name="RAG", definition="Basic definition.")
        enricher._concept_manager.find_fiche_by_name.return_value = concept_path

        with patch.object(
            enricher,
            "_call_gemini_enrich",
            return_value="# RAG\n\n## Définition\n\nEnriched definition.",
        ):
            result = enricher.enrich_concept("RAG")

        assert result is True
        content = concept_path.read_text(encoding="utf-8")
        assert "Enriched definition" in content

    def test_enrich_concept_gemini_error(self, enricher, vault_path):
        """Erreur Gemini → retourne False."""
        concept_path = make_concept(vault_path, name="RAG")
        enricher._concept_manager.find_fiche_by_name.return_value = concept_path

        with patch.object(enricher, "_call_gemini_enrich", side_effect=RuntimeError("API error")):
            result = enricher.enrich_concept("RAG")

        assert result is False


# ---------------------------------------------------------------------------
# Enricher._load_sources_content
# ---------------------------------------------------------------------------


class TestLoadSourcesContent:
    """Tests pour Enricher._load_sources_content."""

    @pytest.fixture
    def enricher(self, vault_path, mock_settings):
        with patch("src.lint.enricher.get_settings", return_value=mock_settings):
            with patch("src.lint.enricher.ConceptManager"):
                return Enricher()

    def test_load_sources_with_articles(self, enricher, vault_path):
        """Charge le contenu des articles RAW référencés."""
        import frontmatter

        # Create a RAW article
        make_article(vault_path, filename="article-1", content="This is the article content.")

        # Create a concept with sources in frontmatter (format attendu par l'enricher)
        concept_path = _make_concept_with_frontmatter_sources(vault_path, "RAG", ["article-1"])

        post = frontmatter.load(str(concept_path))
        content = enricher._load_sources_content(post)
        assert "article-1" in content

    def test_load_sources_no_sources(self, enricher, vault_path):
        """Pas de sources → contenu vide."""
        import frontmatter

        concept_path = make_concept(vault_path, name="RAG")
        post = frontmatter.load(str(concept_path))
        content = enricher._load_sources_content(post)
        assert content == ""

    def test_load_sources_truncates_long_content(self, enricher, vault_path):
        """Le contenu est tronqué à MAX_SOURCE_CHARS."""
        import frontmatter

        long_content = "x" * (MAX_SOURCE_CHARS + 1000)
        make_article(vault_path, filename="long-article", content=long_content)

        concept_path = _make_concept_with_frontmatter_sources(vault_path, "RAG", ["long-article"])
        post = frontmatter.load(str(concept_path))
        content = enricher._load_sources_content(post)
        assert len(content) <= MAX_SOURCE_CHARS + 200  # Allow margin for headers


# ---------------------------------------------------------------------------
# Enricher._find_raw_file
# ---------------------------------------------------------------------------


class TestFindRawFile:
    """Tests pour Enricher._find_raw_file."""

    @pytest.fixture
    def enricher(self, vault_path, mock_settings):
        with patch("src.lint.enricher.get_settings", return_value=mock_settings):
            with patch("src.lint.enricher.ConceptManager"):
                return Enricher()

    def test_find_existing_file(self, enricher, vault_path):
        """Trouve un fichier RAW par son stem."""
        make_article(vault_path, filename="my-article", content="Content.")

        result = enricher._find_raw_file("my-article")
        assert result is not None
        assert result.stem == "my-article"

    def test_find_case_insensitive(self, enricher, vault_path):
        """Recherche insensible à la casse."""
        make_article(vault_path, filename="My-Article", content="Content.")

        result = enricher._find_raw_file("my-article")
        assert result is not None

    def test_find_nonexistent_file(self, enricher, vault_path):
        """Fichier inexistant → None."""
        result = enricher._find_raw_file("nonexistent")
        assert result is None

    def test_find_raw_dir_missing(self, enricher, vault_path):
        """Répertoire RAW inexistant → None."""
        import shutil

        shutil.rmtree(vault_path / "00_RAW")

        result = enricher._find_raw_file("anything")
        assert result is None
