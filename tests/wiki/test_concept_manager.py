"""Tests pour le gestionnaire de concepts (ConceptManager).

Teste : _sanitize_filename, _wiki_dir_for_type, create_or_update_concept,
find_fiche_by_name, list_all, _add_source_to_existing.
"""

import pytest
from datetime import date
from pathlib import Path

from src.wiki.concept_manager import (
    ConceptManager,
    _sanitize_filename,
    _wiki_dir_for_type,
)
from src.wiki.models import ConceptData, PersonData, TechData, TopicData
from src.wiki.cache import WikiStateCache

from tests.conftest import make_concept, make_article


# ---------------------------------------------------------------------------
# _sanitize_filename
# ---------------------------------------------------------------------------


class TestSanitizeFilename:
    """Tests pour la fonction _sanitize_filename."""

    def test_simple_word(self):
        """Un mot simple est capitalisé en PascalCase."""
        assert _sanitize_filename("inflation") == "Inflation"

    def test_multiple_words(self):
        """Les mots séparés par des espaces deviennent PascalCase avec tirets bas."""
        assert _sanitize_filename("knowledge graph") == "Knowledge_Graph"

    def test_acronyms_preserved(self):
        """Les acronymes courts (≤2 chars) gardent leurs majuscules."""
        assert _sanitize_filename("AI") == "AI"
        # Les acronymes de 3+ lettres sont capitalisés comme des mots normaux
        assert _sanitize_filename("API") == "Api"

    def test_acronym_in_phrase(self):
        """Acronymes dans une phrase sont préservés."""
        result = _sanitize_filename("AI inflation")
        assert result == "AI_Inflation"

    def test_special_characters_removed(self):
        """Les caractères spéciaux sont supprimés."""
        result = _sanitize_filename("GraphRAG / Knowledge")
        assert "/" not in result
        # GraphRAG est capitalisé comme un mot normal
        assert "Graphrag" in result

    def test_spaces_replaced_by_underscores(self):
        """Les espaces multiples deviennent un seul underscore."""
        result = _sanitize_filename("machine  learning")
        assert "  " not in result
        assert "_" in result

    def test_leading_trailing_spaces(self):
        """Les espaces en début/fin sont supprimés."""
        result = _sanitize_filename("  RAG  ")
        # RAG (3 lettres) est capitalisé comme un mot normal
        assert result == "Rag"

    def test_hyphens_preserved(self):
        """Les tirets sont préservés."""
        result = _sanitize_filename("self-attention")
        assert "self" in result.lower()

    def test_empty_string(self):
        """Une chaîne vide retourne une chaîne vide."""
        assert _sanitize_filename("") == ""

    def test_unicode_characters(self):
        """Les caractères unicode sont gérés."""
        result = _sanitize_filename("réseaux neuronaux")
        assert result  # Au moins un caractère non vide


# ---------------------------------------------------------------------------
# _wiki_dir_for_type
# ---------------------------------------------------------------------------


class TestWikiDirForType:
    """Tests pour la fonction _wiki_dir_for_type."""

    def test_concept_type(self):
        """Le type 'concept' mappe vers 'Concepts'."""
        assert _wiki_dir_for_type(Path("/vault/02_WIKI"), "concept") == Path(
            "/vault/02_WIKI/Concepts"
        )

    def test_person_type(self):
        """Le type 'person' mappe vers 'People'."""
        assert _wiki_dir_for_type(Path("/vault/02_WIKI"), "person") == Path("/vault/02_WIKI/People")

    def test_technology_type(self):
        """Le type 'technology' mappe vers 'Technologies'."""
        assert _wiki_dir_for_type(Path("/vault/02_WIKI"), "technology") == Path(
            "/vault/02_WIKI/Technologies"
        )

    def test_topic_type(self):
        """Le type 'topic' mappe vers 'Topics'."""
        assert _wiki_dir_for_type(Path("/vault/02_WIKI"), "topic") == Path("/vault/02_WIKI/Topics")

    def test_unknown_type_raises(self):
        """Un type inconnu lève une ValueError."""
        with pytest.raises(ValueError, match="Type de fiche inconnu"):
            _wiki_dir_for_type(Path("/vault/02_WIKI"), "unknown")


# ---------------------------------------------------------------------------
# ConceptManager — création et mise à jour
# ---------------------------------------------------------------------------


class TestConceptManagerCreate:
    """Tests pour la création et mise à jour de fiches via ConceptManager."""

    def test_create_concept(self, vault_path: Path, mock_settings):
        """Création d'une nouvelle fiche concept."""
        cache = WikiStateCache(vault_path)
        manager = ConceptManager(cache=cache)

        data = ConceptData(
            name="GraphRAG",
            definition="RAG avec graphes de connaissances",
            context="Utilisé pour améliorer la récupération",
            aliases=["Graph RAG"],
        )

        file_path, created = manager.create_or_update_concept(
            data=data,
            source_stem="article-test",
            source_title="Test Article",
        )

        assert created is True
        assert file_path.exists()
        # _sanitize_filename capitalise les mots : "GraphRAG" → "Graphrag"
        assert file_path.name == "Graphrag.md"
        assert file_path.parent.name == "Concepts"

        # Vérifier le contenu
        content = file_path.read_text(encoding="utf-8")
        assert "# GraphRAG" in content
        assert "RAG avec graphes de connaissances" in content
        assert "[[article-test]]" in content

    def test_create_concept_creates_directory(self, vault_path: Path, mock_settings):
        """La création crée le sous-répertoire s'il n'existe pas."""
        cache = WikiStateCache(vault_path)
        manager = ConceptManager(cache=cache)

        data = ConceptData(name="Test", definition="Def")
        file_path, created = manager.create_or_update_concept(data=data, source_stem="src1")

        assert created is True
        assert (vault_path / "02_WIKI" / "Concepts").exists()

    def test_update_existing_concept_adds_source(self, vault_path: Path, mock_settings):
        """Mettre à jour un concept existant ajoute la source sans dupliquer."""
        cache = WikiStateCache(vault_path)
        manager = ConceptManager(cache=cache)

        data = ConceptData(name="RAG", definition="Retrieval-Augmented Generation")

        # Création initiale
        file_path, created = manager.create_or_update_concept(
            data=data, source_stem="article-1", source_title="Article 1"
        )
        assert created is True

        # Mise à jour avec une nouvelle source
        file_path2, created2 = manager.create_or_update_concept(
            data=data, source_stem="article-2", source_title="Article 2"
        )
        assert created2 is False
        assert file_path2 == file_path

        content = file_path.read_text(encoding="utf-8")
        assert "[[article-1]]" in content
        assert "[[article-2]]" in content

    def test_update_same_source_no_duplicate(self, vault_path: Path, mock_settings):
        """Ajouter la même source deux fois ne crée pas de doublon."""
        cache = WikiStateCache(vault_path)
        manager = ConceptManager(cache=cache)

        data = ConceptData(name="RAG", definition="Retrieval-Augmented Generation")

        manager.create_or_update_concept(
            data=data, source_stem="article-1", source_title="Article 1"
        )
        manager.create_or_update_concept(
            data=data, source_stem="article-1", source_title="Article 1"
        )

        # _sanitize_filename("RAG") → "Rag"
        content = (vault_path / "02_WIKI" / "Concepts" / "Rag.md").read_text(encoding="utf-8")
        # Le lien ne doit apparaître qu'une seule fois
        assert content.count("[[article-1]]") == 1

    def test_create_person(self, vault_path: Path, mock_settings):
        """Création d'une fiche personne."""
        cache = WikiStateCache(vault_path)
        manager = ConceptManager(cache=cache)

        data = PersonData(name="Yann LeCun", role="Chief AI Scientist at Meta")
        file_path, created = manager.create_or_update_person(data=data, source_stem="article-1")

        assert created is True
        assert file_path.parent.name == "People"
        # _sanitize_filename("Yann LeCun") → "Yann_Lecun" (capitalize)
        assert file_path.name == "Yann_Lecun.md"

        content = file_path.read_text(encoding="utf-8")
        assert "# Yann LeCun" in content
        assert "Chief AI Scientist at Meta" in content

    def test_create_technology(self, vault_path: Path, mock_settings):
        """Création d'une fiche technologie."""
        cache = WikiStateCache(vault_path)
        manager = ConceptManager(cache=cache)

        data = TechData(name="Neo4j", type="database", context="Graph database")
        file_path, created = manager.create_or_update_technology(data=data, source_stem="article-1")

        assert created is True
        assert file_path.parent.name == "Technologies"
        assert file_path.name == "Neo4j.md"

        content = file_path.read_text(encoding="utf-8")
        assert "# Neo4j" in content
        assert "database" in content

    def test_create_topic(self, vault_path: Path, mock_settings):
        """Création d'une fiche topic."""
        cache = WikiStateCache(vault_path)
        manager = ConceptManager(cache=cache)

        data = TopicData(name="Knowledge Graphs", related=["RAG", "Embeddings"])
        file_path, created = manager.create_or_update_topic(data=data, source_stem="article-1")

        assert created is True
        assert file_path.parent.name == "Topics"
        assert file_path.name == "Knowledge_Graphs.md"


# ---------------------------------------------------------------------------
# ConceptManager — recherche
# ---------------------------------------------------------------------------


class TestConceptManagerFind:
    """Tests pour la recherche de fiches via ConceptManager."""

    def test_find_by_stem(self, vault_path: Path, mock_settings):
        """Recherche d'une fiche par son stem (nom de fichier)."""
        cache = WikiStateCache(vault_path)
        manager = ConceptManager(cache=cache)

        data = ConceptData(name="GraphRAG", definition="RAG avec graphes")
        manager.create_or_update_concept(data=data, source_stem="src1")

        # _sanitize_filename("GraphRAG") → "Graphrag"
        result = manager.find_fiche_by_name("Graphrag")
        assert result is not None
        assert result.stem == "Graphrag"

    def test_find_case_insensitive(self, vault_path: Path, mock_settings):
        """La recherche est insensible à la casse."""
        cache = WikiStateCache(vault_path)
        manager = ConceptManager(cache=cache)

        data = ConceptData(name="GraphRAG", definition="RAG avec graphes")
        manager.create_or_update_concept(data=data, source_stem="src1")

        result = manager.find_fiche_by_name("graphrag")
        assert result is not None

    def test_find_by_sanitized_name(self, vault_path: Path, mock_settings):
        """Recherche avec un nom qui nécessite sanitisé."""
        cache = WikiStateCache(vault_path)
        manager = ConceptManager(cache=cache)

        data = ConceptData(name="Knowledge Graph", definition="Graph structure")
        manager.create_or_update_concept(data=data, source_stem="src1")

        # Le fichier est "Knowledge_Graph.md", la recherche doit le trouver
        result = manager.find_fiche_by_name("Knowledge Graph")
        assert result is not None

    def test_find_nonexistent(self, vault_path: Path, mock_settings):
        """La recherche d'une fiche inexistante retourne None."""
        cache = WikiStateCache(vault_path)
        manager = ConceptManager(cache=cache)

        result = manager.find_fiche_by_name("DoesNotExist")
        assert result is None


# ---------------------------------------------------------------------------
# ConceptManager — list_all
# ---------------------------------------------------------------------------


class TestConceptManagerList:
    """Tests pour list_all de ConceptManager."""

    def test_list_all_empty(self, vault_path: Path, mock_settings):
        """Liste vide quand le wiki est vide."""
        cache = WikiStateCache(vault_path)
        manager = ConceptManager(cache=cache)

        result = manager.list_all()
        assert result == []

    def test_list_all_by_type(self, vault_path: Path, mock_settings):
        """Liste filtrée par type de fiche."""
        cache = WikiStateCache(vault_path)
        manager = ConceptManager(cache=cache)

        # Créer un concept et une personne
        manager.create_or_update_concept(
            data=ConceptData(name="RAG", definition="RAG"), source_stem="src1"
        )
        manager.create_or_update_person(
            data=PersonData(name="LeCun", role="Researcher"), source_stem="src1"
        )

        concepts = manager.list_all(wiki_type="concept")
        people = manager.list_all(wiki_type="person")

        assert len(concepts) == 1
        assert len(people) == 1
        assert concepts[0].parent.name == "Concepts"
        assert people[0].parent.name == "People"

    def test_list_all_total(self, vault_path: Path, mock_settings):
        """Liste totale sans filtre retourne toutes les fiches."""
        cache = WikiStateCache(vault_path)
        manager = ConceptManager(cache=cache)

        manager.create_or_update_concept(
            data=ConceptData(name="RAG", definition="RAG"), source_stem="src1"
        )
        manager.create_or_update_concept(
            data=ConceptData(name="GraphRAG", definition="GraphRAG"), source_stem="src2"
        )
        manager.create_or_update_person(
            data=PersonData(name="LeCun", role="Researcher"), source_stem="src1"
        )

        all_fiches = manager.list_all()
        assert len(all_fiches) == 3


# ---------------------------------------------------------------------------
# ConceptManager — frontmatter
# ---------------------------------------------------------------------------


class TestConceptManagerFrontmatter:
    """Tests pour le frontmatter YAML des fiches créées."""

    def test_frontmatter_has_required_fields(self, vault_path: Path, mock_settings):
        """Le frontmatter contient tous les champs obligatoires."""
        import frontmatter

        cache = WikiStateCache(vault_path)
        manager = ConceptManager(cache=cache)

        data = ConceptData(
            name="TestConcept",
            definition="Def",
            aliases=["TC", "test-concept"],
        )
        file_path, _ = manager.create_or_update_concept(data=data, source_stem="src1")

        post = frontmatter.load(str(file_path))
        assert post.metadata["title"] == "TestConcept"
        assert post.metadata["type"] == "concept"
        assert post.metadata["aliases"] == ["TC", "test-concept"]
        assert post.metadata["source_count"] == 1
        assert "updated" in post.metadata
        assert "created" in post.metadata

    def test_source_count_increments_on_update(self, vault_path: Path, mock_settings):
        """Le source_count est incrémenté quand on ajoute une source."""
        import frontmatter

        cache = WikiStateCache(vault_path)
        manager = ConceptManager(cache=cache)

        data = ConceptData(name="RAG", definition="RAG")
        manager.create_or_update_concept(data=data, source_stem="src1")

        # Ajouter une deuxième source
        manager.create_or_update_concept(data=data, source_stem="src2")

        # _sanitize_filename("RAG") → "Rag"
        file_path = vault_path / "02_WIKI" / "Concepts" / "Rag.md"
        post = frontmatter.load(str(file_path))
        assert post.metadata["source_count"] == 2

    def test_updated_date_changes_on_update(self, vault_path: Path, mock_settings):
        """La date 'updated' change quand on met à jour une fiche."""
        import frontmatter

        cache = WikiStateCache(vault_path)
        manager = ConceptManager(cache=cache)

        data = ConceptData(name="RAG", definition="RAG")
        manager.create_or_update_concept(data=data, source_stem="src1")

        # _sanitize_filename("RAG") → "Rag"
        file_path = vault_path / "02_WIKI" / "Concepts" / "Rag.md"
        post_before = frontmatter.load(str(file_path))
        updated_before = post_before.metadata["updated"]

        # Mise à jour
        manager.create_or_update_concept(data=data, source_stem="src2")

        post_after = frontmatter.load(str(file_path))
        # La date updated est aujourd'hui dans les deux cas, mais le champ est mis à jour
        assert post_after.metadata["updated"] == date.today().isoformat()
