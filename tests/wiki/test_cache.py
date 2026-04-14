"""Tests pour le cache persistant du wiki (WikiStateCache).

Teste : persistance, articles, fiches, backlinks, reconstruction.
"""

import json
import pytest
from pathlib import Path

from src.wiki.cache import WikiStateCache, CACHE_FILENAME, CACHE_VERSION

from tests.conftest import make_concept, make_article


# ---------------------------------------------------------------------------
# Initialisation et persistance
# ---------------------------------------------------------------------------


class TestCacheInit:
    """Tests pour l'initialisation du cache."""

    def test_empty_cache_on_new_vault(self, vault_path: Path):
        """Un vault sans cache existant démarre avec un cache vide."""
        cache = WikiStateCache(vault_path)
        assert cache.is_empty()

    def test_cache_creates_on_save(self, vault_path: Path):
        """save() crée le fichier .wiki_state.json."""
        cache = WikiStateCache(vault_path)
        cache.save()

        cache_path = vault_path / CACHE_FILENAME
        assert cache_path.exists()

    def test_cache_persists_and_loads(self, vault_path: Path):
        """Les données sont persistées et rechargées correctement."""
        cache = WikiStateCache(vault_path)

        # Écrire des données
        article_path = vault_path / "00_RAW" / "articles" / "medium" / "test.md"
        article_path.parent.mkdir(parents=True, exist_ok=True)
        article_path.write_text("---\ntitle: Test\n---\n\nContent", encoding="utf-8")

        cache.set_article_state(article_path, wiki_compiled=True, concepts=["RAG"])
        cache.save()

        # Recharger
        cache2 = WikiStateCache(vault_path)
        state = cache2.get_article_state(article_path)
        assert state is not None
        assert state["wiki_compiled"] is True
        assert state["concepts"] == ["RAG"]

    def test_cache_version_mismatch(self, vault_path: Path):
        """Un cache avec une version différente est reconstruit à vide."""
        # Écrire un cache avec une version obsolète
        cache_path = vault_path / CACHE_FILENAME
        old_data = {"version": 999, "articles": {"old": "data"}, "wiki_fiches": {}, "backlinks": {}}
        cache_path.write_text(json.dumps(old_data), encoding="utf-8")

        cache = WikiStateCache(vault_path)
        # Le cache doit être reconstruit à vide
        assert cache.is_empty()

    def test_cache_corrupt_json(self, vault_path: Path):
        """Un cache avec du JSON corrompu est reconstruit à vide."""
        cache_path = vault_path / CACHE_FILENAME
        cache_path.write_text("{{invalid json", encoding="utf-8")

        cache = WikiStateCache(vault_path)
        assert cache.is_empty()


# ---------------------------------------------------------------------------
# Articles (état de compilation)
# ---------------------------------------------------------------------------


class TestCacheArticles:
    """Tests pour la gestion des états d'articles."""

    def test_set_and_get_article_state(self, vault_path: Path):
        """Enregistrer et récupérer l'état d'un article."""
        cache = WikiStateCache(vault_path)
        article_path = vault_path / "00_RAW" / "articles" / "medium" / "test.md"
        article_path.parent.mkdir(parents=True, exist_ok=True)
        article_path.write_text("content", encoding="utf-8")

        cache.set_article_state(article_path, wiki_compiled=True, concepts=["RAG", "GraphRAG"])

        state = cache.get_article_state(article_path)
        assert state is not None
        assert state["wiki_compiled"] is True
        assert state["concepts"] == ["RAG", "GraphRAG"]

    def test_get_nonexistent_article(self, vault_path: Path):
        """Récupérer l'état d'un article inexistant retourne None."""
        cache = WikiStateCache(vault_path)
        fake_path = vault_path / "nonexistent.md"
        assert cache.get_article_state(fake_path) is None

    def test_is_article_modified_new(self, vault_path: Path):
        """Un article non dans le cache est considéré comme modifié."""
        cache = WikiStateCache(vault_path)
        article_path = vault_path / "00_RAW" / "articles" / "medium" / "new.md"
        article_path.parent.mkdir(parents=True, exist_ok=True)
        article_path.write_text("new content", encoding="utf-8")

        assert cache.is_article_modified(article_path) is True

    def test_is_article_modified_unchanged(self, vault_path: Path):
        """Un article non modifié depuis le cache n'est pas marqué modifié."""
        cache = WikiStateCache(vault_path)
        article_path = vault_path / "00_RAW" / "articles" / "medium" / "stable.md"
        article_path.parent.mkdir(parents=True, exist_ok=True)
        article_path.write_text("stable content", encoding="utf-8")

        cache.set_article_state(article_path, wiki_compiled=True)
        assert cache.is_article_modified(article_path) is False

    def test_is_article_modified_after_change(self, vault_path: Path):
        """Un article modifié après le cache est détecté."""
        import time

        cache = WikiStateCache(vault_path)
        article_path = vault_path / "00_RAW" / "articles" / "medium" / "changed.md"
        article_path.parent.mkdir(parents=True, exist_ok=True)
        article_path.write_text("original content", encoding="utf-8")

        cache.set_article_state(article_path, wiki_compiled=True)

        # Modifier le contenu (et forcer un mtime différent)
        time.sleep(0.05)
        article_path.write_text("modified content", encoding="utf-8")

        assert cache.is_article_modified(article_path) is True

    def test_compilation_stats(self, vault_path: Path):
        """Les statistiques de compilation sont correctes."""
        cache = WikiStateCache(vault_path)

        # Créer des fichiers articles
        for i in range(3):
            path = vault_path / "00_RAW" / "articles" / "medium" / f"article_{i}.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"content {i}", encoding="utf-8")
            cache.set_article_state(path, wiki_compiled=(i < 2))

        stats = cache.get_compilation_stats_from_cache()
        assert stats["total_cached"] == 3
        assert stats["total_compiled"] == 2
        assert stats["pending_count"] == 1


# ---------------------------------------------------------------------------
# Fiches wiki (index stem → métadonnées)
# ---------------------------------------------------------------------------


class TestCacheFiches:
    """Tests pour la gestion des fiches wiki dans le cache."""

    def test_set_and_get_fiche_state(self, vault_path: Path):
        """Enregistrer et récupérer l'état d'une fiche."""
        cache = WikiStateCache(vault_path)
        fiche_path = vault_path / "02_WIKI" / "Concepts" / "RAG.md"
        fiche_path.parent.mkdir(parents=True, exist_ok=True)
        fiche_path.write_text("---\ntitle: RAG\n---\n\nContent", encoding="utf-8")

        cache.set_fiche_state(fiche_path, wiki_type="concept", source_count=3, title="RAG")

        state = cache.get_fiche_state("RAG")
        assert state is not None
        assert state["type"] == "concept"
        assert state["source_count"] == 3
        assert state["title"] == "RAG"

    def test_get_fiche_path(self, vault_path: Path):
        """Récupérer le chemin absolu d'une fiche depuis le cache."""
        cache = WikiStateCache(vault_path)
        fiche_path = vault_path / "02_WIKI" / "Concepts" / "RAG.md"
        fiche_path.parent.mkdir(parents=True, exist_ok=True)
        fiche_path.write_text("---\ntitle: RAG\n---\n\nContent", encoding="utf-8")

        cache.set_fiche_state(fiche_path, wiki_type="concept", source_count=1, title="RAG")

        result = cache.get_fiche_path("RAG")
        assert result is not None
        assert result == fiche_path

    def test_get_nonexistent_fiche(self, vault_path: Path):
        """Récupérer une fiche inexistante retourne None."""
        cache = WikiStateCache(vault_path)
        assert cache.get_fiche_state("DoesNotExist") is None
        assert cache.get_fiche_path("DoesNotExist") is None

    def test_get_all_fiche_stems(self, vault_path: Path):
        """Récupérer tous les stems de fiches."""
        cache = WikiStateCache(vault_path)

        for name in ["RAG", "GraphRAG", "Embeddings"]:
            fiche_path = vault_path / "02_WIKI" / "Concepts" / f"{name}.md"
            fiche_path.parent.mkdir(parents=True, exist_ok=True)
            fiche_path.write_text(f"---\ntitle: {name}\n---\n\nContent", encoding="utf-8")
            cache.set_fiche_state(fiche_path, wiki_type="concept", source_count=1, title=name)

        stems = cache.get_all_fiche_stems()
        assert "RAG" in stems
        assert "GraphRAG" in stems
        assert "Embeddings" in stems

    def test_total_wiki_fiches(self, vault_path: Path):
        """Le compteur total de fiches est correct."""
        cache = WikiStateCache(vault_path)

        for name in ["RAG", "GraphRAG"]:
            fiche_path = vault_path / "02_WIKI" / "Concepts" / f"{name}.md"
            fiche_path.parent.mkdir(parents=True, exist_ok=True)
            fiche_path.write_text(f"---\ntitle: {name}\n---\n\nContent", encoding="utf-8")
            cache.set_fiche_state(fiche_path, wiki_type="concept", source_count=1, title=name)

        assert cache.get_total_wiki_fiches() == 2


# ---------------------------------------------------------------------------
# Backlinks (index inversé)
# ---------------------------------------------------------------------------


class TestCacheBacklinks:
    """Tests pour l'index inversé des backlinks."""

    def test_add_and_get_backlinks(self, vault_path: Path):
        """Ajouter et récupérer des backlinks."""
        cache = WikiStateCache(vault_path)

        cache.add_backlink("RAG", "article-1")
        cache.add_backlink("RAG", "article-2")
        cache.add_backlink("GraphRAG", "article-1")

        assert cache.get_backlinks("RAG") == ["article-1", "article-2"]
        assert cache.get_backlinks("GraphRAG") == ["article-1"]

    def test_no_duplicate_backlinks(self, vault_path: Path):
        """Ajouter le même backlink deux fois ne crée pas de doublon."""
        cache = WikiStateCache(vault_path)

        cache.add_backlink("RAG", "article-1")
        cache.add_backlink("RAG", "article-1")

        assert len(cache.get_backlinks("RAG")) == 1

    def test_get_backlinks_nonexistent(self, vault_path: Path):
        """Récupérer les backlinks d'un concept inexistant retourne []."""
        cache = WikiStateCache(vault_path)
        assert cache.get_backlinks("DoesNotExist") == []

    def test_set_backlinks_replaces(self, vault_path: Path):
        """set_backlinks remplace complètement les backlinks."""
        cache = WikiStateCache(vault_path)

        cache.add_backlink("RAG", "article-1")
        cache.set_backlinks("RAG", ["article-3", "article-4"])

        assert cache.get_backlinks("RAG") == ["article-3", "article-4"]


# ---------------------------------------------------------------------------
# Reconstruction
# ---------------------------------------------------------------------------


class TestCacheRebuild:
    """Tests pour la reconstruction du cache depuis le disque."""

    def test_rebuild_fiches_index(self, vault_path: Path):
        """Reconstruire l'index des fiches depuis le disque."""
        # Créer des fiches wiki
        make_concept(vault_path, name="RAG", wiki_type="concept", definition="RAG def")
        make_concept(vault_path, name="LeCun", wiki_type="person", definition="AI researcher")

        cache = WikiStateCache(vault_path)
        count = cache.rebuild_fiches_index(vault_path / "02_WIKI")

        assert count == 2
        assert cache.get_total_wiki_fiches() == 2

        # Vérifier que les fiches sont indexées
        # _sanitize_filename("RAG") → "Rag", _sanitize_filename("LeCun") → "Lecun"
        rag_state = cache.get_fiche_state("Rag")
        assert rag_state is not None
        assert rag_state["type"] == "concept"

    def test_rebuild_articles_index(self, vault_path: Path):
        """Reconstruire l'index des articles depuis le disque."""
        # Créer des articles
        make_article(vault_path, source="medium", filename="article-1", title="Article 1")
        make_article(vault_path, source="medium", filename="article-2", title="Article 2")

        cache = WikiStateCache(vault_path)
        count = cache.rebuild_articles_index(vault_path / "00_RAW")

        assert count == 2

    def test_rebuild_backlinks_index(self, vault_path: Path):
        """Reconstruire l'index des backlinks depuis le disque."""
        # Créer des fiches avec des liens
        make_concept(
            vault_path,
            name="RAG",
            wiki_type="concept",
            definition="RAG def",
            sources=["article-1", "article-2"],
        )

        cache = WikiStateCache(vault_path)
        count = cache.rebuild_backlinks_index(vault_path / "02_WIKI")

        assert count > 0

    def test_rebuild_all(self, vault_path: Path):
        """Reconstruction complète du cache."""
        # Créer des données
        make_article(vault_path, source="medium", filename="article-1", title="Article 1")
        make_concept(vault_path, name="RAG", wiki_type="concept", definition="RAG def")

        cache = WikiStateCache(vault_path)
        cache.rebuild_all()

        # Le cache ne doit pas être vide
        assert not cache.is_empty()

    def test_rebuild_empty_vault(self, vault_path: Path):
        """Reconstruire sur un vault vide ne plante pas."""
        cache = WikiStateCache(vault_path)
        cache.rebuild_all()
        assert cache.is_empty()
