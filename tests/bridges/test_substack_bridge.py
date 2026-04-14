"""Tests unitaires pour le bridge Substack.

Couvre :
- SubstackSyncResult : defaults, total_synced, total_skipped, summary
- SubstackBridge.sync_all : synchronisation avec déduplication
- SubstackBridge._is_newsletter : détection du type de contenu
- SubstackBridge._copy_with_metadata : copie avec enrichissement
"""

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from bridges.substack_bridge import SubstackBridge, SubstackSyncResult


# ---------------------------------------------------------------------------
# SubstackSyncResult
# ---------------------------------------------------------------------------


class TestSubstackSyncResult:
    """Tests pour SubstackSyncResult."""

    def test_defaults(self):
        result = SubstackSyncResult()
        assert result.posts_synced == []
        assert result.posts_skipped == []
        assert result.newsletters_synced == []
        assert result.newsletters_skipped == []
        assert result.errors == []

    def test_total_synced(self):
        result = SubstackSyncResult(
            posts_synced=[Path("a.md")],
            newsletters_synced=[Path("b.md"), Path("c.md")],
        )
        assert result.total_synced == 3

    def test_total_skipped(self):
        result = SubstackSyncResult(
            posts_skipped=[Path("a.md")],
            newsletters_skipped=[Path("b.md")],
        )
        assert result.total_skipped == 2

    def test_summary(self):
        result = SubstackSyncResult(
            posts_synced=[Path("a.md")],
            newsletters_synced=[Path("b.md")],
            posts_skipped=[Path("c.md")],
        )
        summary = result.summary()
        assert "1 posts" in summary
        assert "1 newsletters" in summary


# ---------------------------------------------------------------------------
# SubstackBridge._is_newsletter
# ---------------------------------------------------------------------------


class TestSubstackBridgeIsNewsletter:
    """Tests pour SubstackBridge._is_newsletter."""

    @pytest.fixture
    def bridge(self, vault_path, mock_settings):
        with patch("bridges.substack_bridge.get_settings", return_value=mock_settings):
            return SubstackBridge()

    def test_newsletter_path(self, bridge):
        """Fichier dans newsletters/ → True."""
        path = Path("/data/substack/output/newsletters/weekly-1.md")
        assert bridge._is_newsletter(path) is True

    def test_post_path(self, bridge):
        """Fichier dans posts/ → False."""
        path = Path("/data/substack/output/posts/article-1.md")
        assert bridge._is_newsletter(path) is False

    def test_root_path(self, bridge):
        """Fichier à la racine → False."""
        path = Path("/data/substack/output/article.md")
        assert bridge._is_newsletter(path) is False


# ---------------------------------------------------------------------------
# SubstackBridge.sync_all
# ---------------------------------------------------------------------------


class TestSubstackBridgeSync:
    """Tests pour SubstackBridge.sync_all."""

    @pytest.fixture
    def bridge(self, vault_path, mock_settings):
        """Crée un SubstackBridge avec un répertoire source temporaire."""
        source_dir = vault_path / "external" / "substack"
        posts_dir = source_dir / "posts"
        newsletters_dir = source_dir / "newsletters"
        posts_dir.mkdir(parents=True, exist_ok=True)
        newsletters_dir.mkdir(parents=True, exist_ok=True)

        with patch("bridges.substack_bridge.get_settings", return_value=mock_settings):
            bridge = SubstackBridge()
        bridge.source_dir = source_dir
        bridge.dest_dir = vault_path / "00_RAW" / "articles" / "substack"
        return bridge

    def _make_substack_file(self, dir_path: Path, filename: str, title: str = "Test"):
        """Crée un fichier markdown Substack."""
        import frontmatter

        post = frontmatter.Post("Article content.", metadata={"title": title, "source": "substack"})
        file_path = dir_path / f"{filename}.md"
        file_path.write_text(frontmatter.dumps(post), encoding="utf-8")
        return file_path

    def test_sync_copies_posts(self, bridge, vault_path):
        """Les posts sont copiés dans posts/."""
        self._make_substack_file(bridge.source_dir / "posts", "post-1", "Post 1")

        result = bridge.sync_all()

        assert len(result.posts_synced) == 1
        assert (bridge.dest_dir / "posts" / "post-1.md").exists()

    def test_sync_copies_newsletters(self, bridge, vault_path):
        """Les newsletters sont copiées dans newsletters/."""
        self._make_substack_file(bridge.source_dir / "newsletters", "weekly-1", "Weekly 1")

        result = bridge.sync_all()

        assert len(result.newsletters_synced) == 1
        assert (bridge.dest_dir / "newsletters" / "weekly-1.md").exists()

    def test_sync_skips_existing(self, bridge, vault_path):
        """Les fichiers déjà présents sont ignorés."""
        self._make_substack_file(bridge.source_dir / "posts", "post-1", "Post 1")
        # Pre-create destination
        dest_posts = bridge.dest_dir / "posts"
        dest_posts.mkdir(parents=True, exist_ok=True)
        (dest_posts / "post-1.md").write_text("existing", encoding="utf-8")

        result = bridge.sync_all()

        assert len(result.posts_skipped) == 1
        assert len(result.posts_synced) == 0

    def test_sync_force_overwrites(self, bridge, vault_path):
        """Avec force=True, les fichiers existants sont écrasés."""
        self._make_substack_file(bridge.source_dir / "posts", "post-1", "Post 1")
        dest_posts = bridge.dest_dir / "posts"
        dest_posts.mkdir(parents=True, exist_ok=True)
        (dest_posts / "post-1.md").write_text("old", encoding="utf-8")

        result = bridge.sync_all(force=True)

        assert len(result.posts_synced) == 1
        assert len(result.posts_skipped) == 0

    def test_sync_source_dir_missing_raises(self, vault_path, mock_settings):
        """Répertoire source inexistant → FileNotFoundError."""
        with patch("bridges.substack_bridge.get_settings", return_value=mock_settings):
            bridge = SubstackBridge()
        bridge.source_dir = vault_path / "nonexistent"

        with pytest.raises(FileNotFoundError, match="Répertoire source"):
            bridge.sync_all()

    def test_sync_mixed_content(self, bridge, vault_path):
        """Posts et newsletters sont correctement séparés."""
        self._make_substack_file(bridge.source_dir / "posts", "post-1", "Post 1")
        self._make_substack_file(bridge.source_dir / "newsletters", "weekly-1", "Weekly 1")

        result = bridge.sync_all()

        assert len(result.posts_synced) == 1
        assert len(result.newsletters_synced) == 1


# ---------------------------------------------------------------------------
# SubstackBridge._copy_with_metadata
# ---------------------------------------------------------------------------


class TestSubstackBridgeCopyMetadata:
    """Tests pour SubstackBridge._copy_with_metadata."""

    @pytest.fixture
    def bridge(self, vault_path, mock_settings):
        with patch("bridges.substack_bridge.get_settings", return_value=mock_settings):
            return SubstackBridge()

    def test_adds_source_and_content_type(self, bridge, vault_path, tmp_path):
        """Ajoute source et content_type si absents."""
        import frontmatter

        source = tmp_path / "test.md"
        post = frontmatter.Post("Content.", metadata={"title": "Test"})
        source.write_text(frontmatter.dumps(post), encoding="utf-8")

        dest = tmp_path / "dest.md"
        bridge._copy_with_metadata(source, dest, content_type="newsletter")

        result = frontmatter.load(str(dest))
        assert result.metadata.get("source") == "substack"
        assert result.metadata.get("content_type") == "newsletter"

    def test_preserves_existing_metadata(self, bridge, vault_path, tmp_path):
        """Préserve les métadonnées existantes."""
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
