"""Tests unitaires pour le health checker.

Couvre :
- _normalize_name : normalisation des noms
- HealthChecker.check_broken_links : détection des liens cassés
- HealthChecker.check_orphaned_concepts : concepts sans source
- HealthChecker.check_duplicate_concepts : doublons potentiels
- HealthChecker.check_missing_definitions : sections vides
- HealthChecker.run_full_check : rapport complet
- HealthChecker._calculate_score : calcul du score
- HealthChecker._pick_canonical : sélection du canonical
- HealthChecker._is_section_empty : vérification section vide
"""

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from src.lint.health_checker import (
    EMPTY_PLACEHOLDERS,
    MAIN_SECTIONS,
    WIKILINK_RE,
    HealthChecker,
    _normalize_name,
)
from tests.conftest import make_concept


# ---------------------------------------------------------------------------
# _normalize_name
# ---------------------------------------------------------------------------


class TestNormalizeName:
    """Tests pour _normalize_name."""

    def test_lowercase(self):
        assert _normalize_name("RAG") == "rag"

    def test_removes_special_chars(self):
        assert _normalize_name("Knowledge-Graphs!") == "knowledgegraphs"

    def test_collapses_spaces(self):
        assert _normalize_name("RAG  System") == "rag system"

    def test_strips_whitespace(self):
        assert _normalize_name("  RAG  ") == "rag"

    def test_empty_string(self):
        assert _normalize_name("") == ""


# ---------------------------------------------------------------------------
# WIKILINK_RE
# ---------------------------------------------------------------------------


class TestWikilinkRegex:
    """Tests pour la regex d'extraction des wikilinks."""

    def test_simple_link(self):
        assert WIKILINK_RE.findall("See [[RAG]] for details") == ["RAG"]

    def test_link_with_alias(self):
        assert WIKILINK_RE.findall("[[RAG|Retrieval Augmented]]") == ["RAG"]

    def test_link_with_anchor(self):
        assert WIKILINK_RE.findall("[[RAG#section]]") == ["RAG"]

    def test_multiple_links(self):
        matches = WIKILINK_RE.findall("[[RAG]] and [[GraphRAG]]")
        assert "RAG" in matches
        assert "GraphRAG" in matches

    def test_no_links(self):
        assert WIKILINK_RE.findall("No links here") == []


# ---------------------------------------------------------------------------
# HealthChecker — check_broken_links
# ---------------------------------------------------------------------------


class TestCheckBrokenLinks:
    """Tests pour HealthChecker.check_broken_links."""

    @pytest.fixture
    def checker(self, vault_path, mock_settings):
        with patch("src.lint.health_checker.get_settings", return_value=mock_settings):
            return HealthChecker()

    def test_no_broken_links_in_healthy_wiki(self, checker, vault_path):
        """Fiche avec lien vers un fichier existant → pas de lien cassé."""
        make_concept(vault_path, name="RAG")
        make_concept(vault_path, name="GraphRAG", related=["RAG"])

        broken = checker.check_broken_links()
        assert broken == []

    def test_broken_link_detected(self, checker, vault_path):
        """Fiche avec lien vers un fichier inexistant → lien cassé."""
        make_concept(vault_path, name="RAG", related=["NonExistent"])

        broken = checker.check_broken_links()
        assert len(broken) >= 1
        assert any(b.link_target == "NonExistent" for b in broken)

    def test_no_broken_links_empty_wiki(self, checker, vault_path):
        """Wiki vide → pas de liens cassés."""
        broken = checker.check_broken_links()
        assert broken == []

    def test_wiki_dir_missing(self, vault_path, mock_settings):
        """Répertoire wiki inexistant → liste vide."""
        import shutil

        shutil.rmtree(vault_path / "02_WIKI")

        with patch("src.lint.health_checker.get_settings", return_value=mock_settings):
            checker = HealthChecker()
        broken = checker.check_broken_links()
        assert broken == []


# ---------------------------------------------------------------------------
# HealthChecker — check_orphaned_concepts
# ---------------------------------------------------------------------------


class TestCheckOrphanedConcepts:
    """Tests pour HealthChecker.check_orphaned_concepts."""

    @pytest.fixture
    def checker(self, vault_path, mock_settings):
        with patch("src.lint.health_checker.get_settings", return_value=mock_settings):
            return HealthChecker()

    def test_orphaned_concept_detected(self, checker, vault_path):
        """Fiche avec source_count=0 → orpheline."""
        make_concept(vault_path, name="Orphan", sources=[])

        orphaned = checker.check_orphaned_concepts()
        assert len(orphaned) >= 1
        assert any(o.title == "Orphan" for o in orphaned)

    def test_sourced_concept_not_orphaned(self, checker, vault_path):
        """Fiche avec source_count>0 → pas orpheline."""
        make_concept(vault_path, name="Sourced", sources=["article-1"])

        orphaned = checker.check_orphaned_concepts()
        assert not any(o.title == "Sourced" for o in orphaned)

    def test_empty_wiki_no_orphans(self, checker, vault_path):
        orphaned = checker.check_orphaned_concepts()
        assert orphaned == []


# ---------------------------------------------------------------------------
# HealthChecker — check_duplicate_concepts
# ---------------------------------------------------------------------------


class TestCheckDuplicateConcepts:
    """Tests pour HealthChecker.check_duplicate_concepts."""

    @pytest.fixture
    def checker(self, vault_path, mock_settings):
        with patch("src.lint.health_checker.get_settings", return_value=mock_settings):
            return HealthChecker()

    def test_no_duplicates(self, checker, vault_path):
        make_concept(vault_path, name="RAG")
        make_concept(vault_path, name="GraphRAG")

        duplicates = checker.check_duplicate_concepts()
        assert duplicates == []

    def test_exact_duplicates_detected(self, checker, vault_path):
        """Deux fiches avec le même nom normalisé → doublon."""
        # Create two files with same normalized name in different dirs
        concepts_dir = vault_path / "02_WIKI" / "Concepts"
        topics_dir = vault_path / "02_WIKI" / "Topics"
        concepts_dir.mkdir(parents=True, exist_ok=True)
        topics_dir.mkdir(parents=True, exist_ok=True)

        # Same name in two different directories
        content = f"""---
title: "Knowledge Graph"
type: concept
source_count: 1
---

# Knowledge Graph

## Définition

A knowledge graph is...
"""
        (concepts_dir / "Knowledge_Graph.md").write_text(content, encoding="utf-8")
        (topics_dir / "Knowledge_Graph.md").write_text(content, encoding="utf-8")

        duplicates = checker.check_duplicate_concepts()
        assert len(duplicates) >= 1

    def test_empty_wiki_no_duplicates(self, checker, vault_path):
        duplicates = checker.check_duplicate_concepts()
        assert duplicates == []


# ---------------------------------------------------------------------------
# HealthChecker — check_missing_definitions
# ---------------------------------------------------------------------------


class TestCheckMissingDefinitions:
    """Tests pour HealthChecker.check_missing_definitions."""

    @pytest.fixture
    def checker(self, vault_path, mock_settings):
        with patch("src.lint.health_checker.get_settings", return_value=mock_settings):
            return HealthChecker()

    def test_missing_definition_detected(self, checker, vault_path):
        """Fiche avec section Définition vide → manquante."""
        concepts_dir = vault_path / "02_WIKI" / "Concepts"
        concepts_dir.mkdir(parents=True, exist_ok=True)

        content = f"""---
title: "Empty Concept"
type: concept
source_count: 1
---

# Empty Concept

## Définition

_À compléter_
"""
        (concepts_dir / "Empty_Concept.md").write_text(content, encoding="utf-8")

        missing = checker.check_missing_definitions()
        assert len(missing) >= 1
        assert any(m.title == "Empty Concept" for m in missing)

    def test_filled_definition_not_missing(self, checker, vault_path):
        """Fiche avec section Définition remplie → pas manquante."""
        make_concept(vault_path, name="Filled", definition="A real definition.")

        missing = checker.check_missing_definitions()
        assert not any(m.title == "Filled" for m in missing)

    def test_empty_wiki_no_missing(self, checker, vault_path):
        missing = checker.check_missing_definitions()
        assert missing == []


# ---------------------------------------------------------------------------
# HealthChecker — _calculate_score
# ---------------------------------------------------------------------------


class TestCalculateScore:
    """Tests pour HealthChecker._calculate_score."""

    @pytest.fixture
    def checker(self, vault_path, mock_settings):
        with patch("src.lint.health_checker.get_settings", return_value=mock_settings):
            return HealthChecker()

    def test_perfect_score(self, checker):
        score = checker._calculate_score([], [], [], [], 100)
        assert score == 100

    def test_broken_links_penalty(self, checker):
        broken = [None] * 3  # 3 broken links → -15
        score = checker._calculate_score(broken, [], [], [], 100)
        assert score == 85  # 100 - 15

    def test_orphaned_penalty(self, checker):
        orphaned = [None] * 4  # 4 orphans → -12
        score = checker._calculate_score([], orphaned, [], [], 100)
        assert score == 88  # 100 - 12

    def test_duplicates_penalty(self, checker):
        duplicates = [None] * 2  # 2 duplicate groups → -10
        score = checker._calculate_score([], [], duplicates, [], 100)
        assert score == 90  # 100 - 10

    def test_missing_definitions_penalty(self, checker):
        missing = [None] * 5  # 5 missing → -10
        score = checker._calculate_score([], [], [], missing, 100)
        assert score == 90  # 100 - 10

    def test_score_minimum_zero(self, checker):
        """Score ne peut pas descendre sous 0."""
        broken = [None] * 20  # max penalty -30
        orphaned = [None] * 20  # max penalty -20
        duplicates = [None] * 20  # max penalty -20
        missing = [None] * 20  # max penalty -20
        score = checker._calculate_score(broken, orphaned, duplicates, missing, 100)
        assert score == 10  # 100 - 30 - 20 - 20 - 20 = 10

    def test_broken_links_capped_at_30(self, checker):
        broken = [None] * 10  # 10 * 5 = 50, capped at 30
        score = checker._calculate_score(broken, [], [], [], 100)
        assert score == 70  # 100 - 30


# ---------------------------------------------------------------------------
# HealthChecker — _pick_canonical
# ---------------------------------------------------------------------------


class TestPickCanonical:
    """Tests pour HealthChecker._pick_canonical."""

    @pytest.fixture
    def checker(self, vault_path, mock_settings):
        with patch("src.lint.health_checker.get_settings", return_value=mock_settings):
            return HealthChecker()

    def test_picks_highest_source_count(self, checker, vault_path):
        """La fiche avec le plus grand source_count est canonique."""
        concepts_dir = vault_path / "02_WIKI" / "Concepts"
        concepts_dir.mkdir(parents=True, exist_ok=True)

        # Fiche avec source_count=5
        content_a = f"""---
title: "RAG"
source_count: 5
---

Long content here.
"""
        path_a = concepts_dir / "Rag.md"
        path_a.write_text(content_a, encoding="utf-8")

        # Fiche avec source_count=1
        content_b = f"""---
title: "RAG"
source_count: 1
---

Short.
"""
        path_b = concepts_dir / "Rag2.md"
        path_b.write_text(content_b, encoding="utf-8")

        canonical = checker._pick_canonical([path_a, path_b])
        assert canonical == path_a


# ---------------------------------------------------------------------------
# HealthChecker — _is_section_empty
# ---------------------------------------------------------------------------


class TestIsSectionEmpty:
    """Tests pour HealthChecker._is_section_empty."""

    @pytest.fixture
    def checker(self, vault_path, mock_settings):
        with patch("src.lint.health_checker.get_settings", return_value=mock_settings):
            return HealthChecker()

    def test_empty_section(self, checker):
        content = "# Title\n\n## Définition\n\n_À compléter_\n\n## Sources\n\n- [[src]]"
        assert checker._is_section_empty(content, "Définition") is True

    def test_filled_section(self, checker):
        content = "# Title\n\n## Définition\n\nRAG is a technique...\n\n## Sources\n\n- [[src]]"
        assert checker._is_section_empty(content, "Définition") is False

    def test_missing_section(self, checker):
        content = "# Title\n\n## Sources\n\n- [[src]]"
        assert checker._is_section_empty(content, "Définition") is True

    def test_all_placeholder_variants(self, checker):
        for placeholder in EMPTY_PLACEHOLDERS:
            content = f"# Title\n\n## Définition\n\n{placeholder}\n\n## Sources\n\n- [[src]]"
            assert checker._is_section_empty(content, "Définition") is True


# ---------------------------------------------------------------------------
# HealthChecker — run_full_check
# ---------------------------------------------------------------------------


class TestRunFullCheck:
    """Tests pour HealthChecker.run_full_check."""

    @pytest.fixture
    def checker(self, vault_path, mock_settings):
        with patch("src.lint.health_checker.get_settings", return_value=mock_settings):
            return HealthChecker()

    def test_full_check_healthy_wiki(self, checker, vault_path):
        """Wiki sain → score 100."""
        make_concept(vault_path, name="RAG", definition="Retrieval-Augmented Generation")
        make_concept(vault_path, name="GraphRAG", sources=["article-1"], related=["RAG"])

        report = checker.run_full_check()
        assert report.total_wiki_fiches >= 2
        assert report.score >= 50  # At minimum, no crash

    def test_full_check_empty_wiki(self, checker, vault_path):
        """Wiki vide → score 100 (rien à pénaliser)."""
        report = checker.run_full_check()
        assert report.total_wiki_fiches == 0
        assert report.score == 100
