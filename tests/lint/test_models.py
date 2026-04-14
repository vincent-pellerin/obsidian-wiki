"""Tests unitaires pour les modèles lint.

Couvre :
- BrokenLink, OrphanedConcept, DuplicateGroup, MissingDefinition
- HealthReport : defaults, is_healthy, summary
"""

from pathlib import Path

from src.lint.models import (
    BrokenLink,
    DuplicateGroup,
    HealthReport,
    MissingDefinition,
    OrphanedConcept,
)


class TestBrokenLink:
    """Tests pour BrokenLink."""

    def test_creation(self):
        link = BrokenLink(source_file=Path("Concepts/Rag.md"), link_target="Missing", line_number=5)
        assert link.source_file == Path("Concepts/Rag.md")
        assert link.link_target == "Missing"
        assert link.line_number == 5

    def test_default_line_number(self):
        link = BrokenLink(source_file=Path("a.md"), link_target="b")
        assert link.line_number == 0


class TestOrphanedConcept:
    """Tests pour OrphanedConcept."""

    def test_creation(self):
        oc = OrphanedConcept(path=Path("Concepts/Rag.md"), title="RAG", wiki_type="concept")
        assert oc.path == Path("Concepts/Rag.md")
        assert oc.title == "RAG"
        assert oc.wiki_type == "concept"


class TestDuplicateGroup:
    """Tests pour DuplicateGroup."""

    def test_creation(self):
        dg = DuplicateGroup(canonical=Path("Concepts/Rag.md"), duplicates=[Path("Concepts/rag.md")])
        assert dg.canonical == Path("Concepts/Rag.md")
        assert len(dg.duplicates) == 1

    def test_default_duplicates_empty(self):
        dg = DuplicateGroup(canonical=Path("a.md"))
        assert dg.duplicates == []


class TestMissingDefinition:
    """Tests pour MissingDefinition."""

    def test_creation(self):
        md = MissingDefinition(path=Path("Concepts/Rag.md"), title="RAG", section="Définition")
        assert md.section == "Définition"


class TestHealthReport:
    """Tests pour HealthReport."""

    def test_defaults(self):
        report = HealthReport()
        assert report.broken_links == []
        assert report.orphaned_concepts == []
        assert report.duplicate_groups == []
        assert report.missing_definitions == []
        assert report.total_wiki_fiches == 0
        assert report.score == 100

    def test_is_healthy_at_80(self):
        report = HealthReport(score=80)
        assert report.is_healthy is True

    def test_is_healthy_below_80(self):
        report = HealthReport(score=79)
        assert report.is_healthy is False

    def test_is_healthy_at_100(self):
        report = HealthReport(score=100)
        assert report.is_healthy is True

    def test_summary_healthy(self):
        report = HealthReport(score=95, total_wiki_fiches=50)
        summary = report.summary()
        assert "95" in summary
        assert "50" in summary

    def test_summary_degraded(self):
        report = HealthReport(score=60, total_wiki_fiches=30)
        summary = report.summary()
        assert "60" in summary

    def test_summary_critical(self):
        report = HealthReport(score=20, total_wiki_fiches=10)
        summary = report.summary()
        assert "20" in summary

    def test_summary_with_issues(self):
        report = HealthReport(
            score=75,
            total_wiki_fiches=100,
            broken_links=[BrokenLink(Path("a.md"), "b", 5)],
            orphaned_concepts=[OrphanedConcept(Path("c.md"), "C", "concept")],
            duplicate_groups=[DuplicateGroup(Path("d.md"), [Path("e.md")])],
            missing_definitions=[MissingDefinition(Path("f.md"), "F", "Définition")],
        )
        summary = report.summary()
        assert "1" in summary  # counts appear in summary
