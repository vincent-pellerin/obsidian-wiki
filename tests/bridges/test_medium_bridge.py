"""Tests unitaires pour le bridge Medium.

Couvre :
- SyncResult : defaults, total, summary
- MediumBridge.sync_to_raw : synchronisation avec déduplication
- MediumBridge.get_pending_articles : articles non compilés
- MediumBridge._copy_with_metadata : copie avec frontmatter
"""

import os
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from bridges.medium_bridge import MediumBridge, SyncResult
from tests.conftest import make_article


# ---------------------------------------------------------------------------
# SyncResult
# ---------------------------------------------------------------------------


class TestSyncResult:
    """Tests pour SyncResult."""

    def test_defaults(self):
        result = SyncResult()
        assert result.synced == []
        assert result.skipped == []
        assert result.errors == []

    def test_total(self):
        result = SyncResult(
            synced=[Path("a.md")],
            skipped=[Path("b.md"), Path("c.md")],
            errors=[(Path("d.md"), "error")],
        )
        assert result.total == 4

    def test_summary(self):
        result = SyncResult(synced=[Path("a.md")], skipped=[Path("b.md")])
        summary = result.summary()
        assert "1 copiés" in summary
        assert "1 ignorés" in summary


# ---------------------------------------------------------------------------
# MediumBridge.sync_to_raw
# ---------------------------------------------------------------------------


class TestMediumBridgeSync:
    """Tests pour MediumBridge.sync_to_raw."""

    @pytest.fixture
    def bridge(self, vault_path, mock_settings):
        """Crée un MediumBridge avec un répertoire source temporaire."""
        source_dir = vault_path / "external" / "medium"
        source_dir.mkdir(parents=True, exist_ok=True)

        # Override source_dir to point to our temp dir
        with patch("bridges.medium_bridge.get_settings", return_value=mock_settings):
            bridge = MediumBridge()
        bridge.source_dir = source_dir
        bridge.dest_dir = vault_path / "00_RAW" / "articles" / "medium"
        return bridge

    def _make_medium_article(
        self, source_dir: Path, filename: str, title: str = "Test", content: str = "Content."
    ):
        """Crée un article Medium dans le répertoire source."""
        metadata = {"title": title, "source": "medium"}
        frontmatter_str = yaml.dump(metadata, allow_unicode=True, default_flow_style=False).strip()
        file_content = f"---\n{frontmatter_str}\n---\n\n{content}\n"
        file_path = source_dir / f"{filename}.md"
        file_path.write_text(file_content, encoding="utf-8")
        return file_path

    def test_sync_copies_new_articles(self, bridge, vault_path):
        """Nouveaux articles sont copiés dans 00_RAW."""
        self._make_medium_article(bridge.source_dir, "article-1", "Article 1")

        result = bridge.sync_to_raw()

        assert len(result.synced) == 1
        assert len(result.skipped) == 0
        assert (bridge.dest_dir / "article-1.md").exists()

    def test_sync_skips_existing_articles(self, bridge, vault_path):
        """Articles déjà présents sont ignorés."""
        self._make_medium_article(bridge.source_dir, "article-1", "Article 1")
        # Pre-create the destination
        bridge.dest_dir.mkdir(parents=True, exist_ok=True)
        (bridge.dest_dir / "article-1.md").write_text("existing", encoding="utf-8")

        result = bridge.sync_to_raw()

        assert len(result.synced) == 0
        assert len(result.skipped) == 1

    def test_sync_force_overwrites(self, bridge, vault_path):
        """Avec force=True, les articles existants sont écrasés."""
        self._make_medium_article(bridge.source_dir, "article-1", "Article 1")
        bridge.dest_dir.mkdir(parents=True, exist_ok=True)
        (bridge.dest_dir / "article-1.md").write_text("old content", encoding="utf-8")

        result = bridge.sync_to_raw(force=True)

        assert len(result.synced) == 1
        assert len(result.skipped) == 0

    def test_sync_source_dir_missing_raises(self, vault_path, mock_settings):
        """Répertoire source inexistant → FileNotFoundError."""
        nonexistent = vault_path / "nonexistent"
        with patch("bridges.medium_bridge.get_settings", return_value=mock_settings):
            bridge = MediumBridge()
        bridge.source_dir = nonexistent

        with pytest.raises(FileNotFoundError, match="Répertoire source"):
            bridge.sync_to_raw()

    def test_sync_multiple_articles(self, bridge, vault_path):
        """Plusieurs articles sont synchronisés."""
        self._make_medium_article(bridge.source_dir, "article-1")
        self._make_medium_article(bridge.source_dir, "article-2")
        self._make_medium_article(bridge.source_dir, "article-3")

        result = bridge.sync_to_raw()

        assert len(result.synced) == 3
        assert result.total == 3


# ---------------------------------------------------------------------------
# MediumBridge.get_pending_articles
# ---------------------------------------------------------------------------


class TestMediumBridgePending:
    """Tests pour MediumBridge.get_pending_articles."""

    @pytest.fixture
    def bridge(self, vault_path, mock_settings):
        with patch("bridges.medium_bridge.get_settings", return_value=mock_settings):
            bridge = MediumBridge()
        bridge.dest_dir = vault_path / "00_RAW" / "articles" / "medium"
        return bridge

    def test_pending_articles_empty_dest(self, bridge, vault_path):
        """Répertoire destination vide → pas d'articles en attente."""
        bridge.dest_dir.mkdir(parents=True, exist_ok=True)
        pending = bridge.get_pending_articles()
        assert pending == []

    def test_pending_articles_all_pending(self, bridge, vault_path):
        """Articles non référencés dans le wiki → tous en attente."""
        bridge.dest_dir.mkdir(parents=True, exist_ok=True)
        (bridge.dest_dir / "article-1.md").write_text("content", encoding="utf-8")
        (bridge.dest_dir / "article-2.md").write_text("content", encoding="utf-8")

        pending = bridge.get_pending_articles()
        assert len(pending) == 2

    def test_pending_articles_dest_missing(self, bridge, vault_path):
        """Répertoire destination inexistant → liste vide."""
        pending = bridge.get_pending_articles()
        assert pending == []


# ---------------------------------------------------------------------------
# MediumBridge._copy_with_metadata
# ---------------------------------------------------------------------------


class TestMediumBridgeCopyMetadata:
    """Tests pour MediumBridge._copy_with_metadata."""

    @pytest.fixture
    def bridge(self, vault_path, mock_settings):
        with patch("bridges.medium_bridge.get_settings", return_value=mock_settings):
            return MediumBridge()

    def test_adds_source_metadata(self, bridge, vault_path, tmp_path):
        """Ajoute la métadonnée source si absente."""
        import frontmatter

        source = tmp_path / "test.md"
        post = frontmatter.Post("Content.", metadata={"title": "Test"})
        source.write_text(frontmatter.dumps(post), encoding="utf-8")

        dest = tmp_path / "dest.md"
        bridge._copy_with_metadata(source, dest)

        result = frontmatter.load(str(dest))
        assert result.metadata.get("source") == "medium"

    def test_preserves_existing_source(self, bridge, vault_path, tmp_path):
        """Préserve la métadonnée source si déjà présente."""
        import frontmatter

        source = tmp_path / "test.md"
        # Use yaml.dump to create proper frontmatter (frontmatter.Post serializes incorrectly)
        metadata_yaml = yaml.dump(
            {"title": "Test", "source": "custom"}, allow_unicode=True, default_flow_style=False
        )
        source.write_text(f"---\n{metadata_yaml}---\n\nContent.\n", encoding="utf-8")

        dest = tmp_path / "dest.md"
        bridge._copy_with_metadata(source, dest)

        result = frontmatter.load(str(dest))
        assert result.metadata.get("source") == "custom"
