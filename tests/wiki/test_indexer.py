"""Tests pour le générateur d'index (Indexer).

Teste : build_master_index, _collect_stats, _render_index.
"""

import pytest
from pathlib import Path

import frontmatter

from src.wiki.indexer import Indexer
from src.wiki.cache import WikiStateCache

from tests.conftest import make_concept


class TestIndexerBuild:
    """Tests pour la génération de l'index maître."""

    def test_build_master_index_creates_file(self, vault_path: Path, mock_settings):
        """build_master_index crée le fichier 000_Master_Index.md."""
        indexer = Indexer()
        result = indexer.build_master_index()

        assert result.exists()
        assert result.name == "000_Master_Index.md"
        assert result.parent.name == "Index"

    def test_build_master_index_empty_wiki(self, vault_path: Path, mock_settings):
        """L'index sur un wiki vide affiche des statistiques à 0."""
        indexer = Indexer()
        result = indexer.build_master_index()

        content = result.read_text(encoding="utf-8")
        assert "Total fiches" in content
        assert "0" in content  # Pas de fiches

    def test_build_master_index_with_concepts(self, vault_path: Path, mock_settings):
        """L'index inclut les concepts créés."""
        # Créer quelques fiches
        make_concept(vault_path, name="RAG", wiki_type="concept", definition="RAG def")
        make_concept(vault_path, name="GraphRAG", wiki_type="concept", definition="GraphRAG def")
        make_concept(vault_path, name="LeCun", wiki_type="person", definition="AI researcher")

        indexer = Indexer()
        result = indexer.build_master_index()

        content = result.read_text(encoding="utf-8")
        # _sanitize_filename capitalise les mots : RAG→Rag, GraphRAG→Graphrag, LeCun→Lecun
        assert "[[Rag]]" in content
        assert "[[Graphrag]]" in content
        assert "[[Lecun]]" in content
        assert "Total fiches" in content

    def test_build_master_index_categories(self, vault_path: Path, mock_settings):
        """L'index organise les fiches par catégorie."""
        make_concept(vault_path, name="RAG", wiki_type="concept", definition="RAG def")
        make_concept(vault_path, name="LeCun", wiki_type="person", definition="AI researcher")
        make_concept(vault_path, name="Neo4j", wiki_type="technology", definition="Graph DB")
        make_concept(vault_path, name="Knowledge Graphs", wiki_type="topic", definition="Topic")

        indexer = Indexer()
        result = indexer.build_master_index()

        content = result.read_text(encoding="utf-8")
        assert "## Concepts" in content
        assert "## Personnes" in content
        assert "## Technologies" in content
        assert "## Topics" in content

    def test_build_master_index_top_connected(self, vault_path: Path, mock_settings):
        """L'index affiche les fiches les plus connectées."""
        # Créer un concept avec beaucoup de sources
        make_concept(
            vault_path,
            name="RAG",
            wiki_type="concept",
            definition="RAG def",
            sources=["src1", "src2", "src3"],
        )
        # Et un concept avec peu de sources
        make_concept(
            vault_path,
            name="GraphRAG",
            wiki_type="concept",
            definition="GraphRAG def",
            sources=["src1"],
        )

        indexer = Indexer()
        result = indexer.build_master_index()

        content = result.read_text(encoding="utf-8")
        assert "Fiches les plus connect" in content

    def test_build_master_index_frontmatter(self, vault_path: Path, mock_settings):
        """L'index a un frontmatter YAML valide."""
        make_concept(vault_path, name="RAG", wiki_type="concept", definition="RAG def")

        indexer = Indexer()
        result = indexer.build_master_index()

        post = frontmatter.load(str(result))
        assert post.metadata["title"] == "Index Maître du Wiki"
        assert post.metadata["type"] == "index"
        assert "updated" in post.metadata

    def test_build_master_index_source_count_suffix(self, vault_path: Path, mock_settings):
        """Les fiches avec source_count > 1 affichent un suffixe (×N)."""
        make_concept(
            vault_path,
            name="RAG",
            wiki_type="concept",
            definition="RAG def",
            sources=["src1", "src2", "src3"],
        )

        indexer = Indexer()
        result = indexer.build_master_index()

        content = result.read_text(encoding="utf-8")
        # Le suffixe (×3) doit apparaître pour RAG avec 3 sources
        assert "×3" in content or "(×3)" in content


# ---------------------------------------------------------------------------
# _collect_stats
# ---------------------------------------------------------------------------


class TestIndexerStats:
    """Tests pour la collecte de statistiques."""

    def test_collect_stats_empty(self, vault_path: Path, mock_settings):
        """Les stats sur un wiki vide sont à 0."""
        indexer = Indexer()
        stats = indexer._collect_stats()

        assert stats["total_fiches"] == 0
        assert stats["total_sources"] == 0

    def test_collect_stats_with_fiches(self, vault_path: Path, mock_settings):
        """Les stats agrègent correctement les fiches et sources."""
        make_concept(
            vault_path,
            name="RAG",
            wiki_type="concept",
            definition="RAG def",
            sources=["src1", "src2"],
        )
        make_concept(
            vault_path,
            name="LeCun",
            wiki_type="person",
            definition="AI researcher",
            sources=["src1"],
        )

        indexer = Indexer()
        stats = indexer._collect_stats()

        assert stats["total_fiches"] == 2
        assert stats["total_sources"] == 3  # 2 + 1

    def test_collect_stats_by_type(self, vault_path: Path, mock_settings):
        """Les stats sont organisées par type de fiche."""
        make_concept(vault_path, name="RAG", wiki_type="concept", definition="RAG def")
        make_concept(vault_path, name="LeCun", wiki_type="person", definition="AI researcher")

        indexer = Indexer()
        stats = indexer._collect_stats()

        assert "concept" in stats["by_type"]
        assert "person" in stats["by_type"]
        assert len(stats["by_type"]["concept"]) == 1
        assert len(stats["by_type"]["person"]) == 1

    def test_collect_stats_top_connected(self, vault_path: Path, mock_settings):
        """Les top connected sont triés par source_count décroissant."""
        make_concept(
            vault_path,
            name="RAG",
            wiki_type="concept",
            definition="RAG def",
            sources=["src1", "src2", "src3"],
        )
        make_concept(
            vault_path,
            name="Embeddings",
            wiki_type="concept",
            definition="Embeddings def",
            sources=["src1"],
        )

        indexer = Indexer()
        stats = indexer._collect_stats()

        assert len(stats["top_connected"]) >= 1
        # RAG (3 sources) doit être en premier
        assert stats["top_connected"][0]["title"] == "RAG"


# ---------------------------------------------------------------------------
# _read_entry_meta
# ---------------------------------------------------------------------------


class TestIndexerReadEntry:
    """Tests pour la lecture des métadonnées d'une fiche."""

    def test_read_entry_meta_valid(self, vault_path: Path, mock_settings):
        """Lire les métadonnées d'une fiche valide."""
        fiche_path = make_concept(vault_path, name="RAG", wiki_type="concept", definition="RAG def")

        indexer = Indexer()
        entry = indexer._read_entry_meta(fiche_path)

        assert entry is not None
        assert entry["title"] == "RAG"
        assert entry["type"] == "concept"
        assert entry["source_count"] >= 0

    def test_read_entry_meta_nonexistent(self, vault_path: Path, mock_settings):
        """Lire les métadonnées d'un fichier inexistant retourne None."""
        indexer = Indexer()
        fake_path = vault_path / "02_WIKI" / "Concepts" / "NonExistent.md"
        entry = indexer._read_entry_meta(fake_path)

        assert entry is None
