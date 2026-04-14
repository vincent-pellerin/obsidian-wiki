"""Tests pour les modèles de données du wiki.

Teste les dataclasses : ConceptData, PersonData, TechData, TopicData,
ExtractedKnowledge, CompilationResult, BatchCompilationResult.
"""

import pytest
from pathlib import Path

from src.wiki.models import (
    BatchCompilationResult,
    CompilationResult,
    ConceptData,
    ExtractedKnowledge,
    PersonData,
    TechData,
    TopicData,
)


# ---------------------------------------------------------------------------
# ConceptData
# ---------------------------------------------------------------------------


class TestConceptData:
    """Tests pour la dataclass ConceptData."""

    def test_creation_minimal(self):
        """Un concept avec nom et définition uniquement."""
        concept = ConceptData(name="GraphRAG", definition="RAG avec graphes de connaissances")
        assert concept.name == "GraphRAG"
        assert concept.definition == "RAG avec graphes de connaissances"
        assert concept.context == ""
        assert concept.aliases == []

    def test_creation_complete(self):
        """Un concept avec tous les champs."""
        concept = ConceptData(
            name="RAG",
            definition="Retrieval-Augmented Generation",
            context="Utilisé pour améliorer les réponses des LLMs",
            aliases=["Retrieval-Augmented Generation", "retrieval augmented generation"],
        )
        assert concept.name == "RAG"
        assert concept.context == "Utilisé pour améliorer les réponses des LLMs"
        assert len(concept.aliases) == 2

    def test_default_values(self):
        """Les champs optionnels ont des valeurs par défaut."""
        concept = ConceptData(name="Test", definition="Def")
        assert concept.context == ""
        assert concept.aliases == []


# ---------------------------------------------------------------------------
# PersonData
# ---------------------------------------------------------------------------


class TestPersonData:
    """Tests pour la dataclass PersonData."""

    def test_creation(self):
        """Création d'une personne avec nom et rôle."""
        person = PersonData(name="Yann LeCun", role="Chief AI Scientist at Meta")
        assert person.name == "Yann LeCun"
        assert person.role == "Chief AI Scientist at Meta"
        assert person.context == ""

    def test_with_context(self):
        """Personne avec contexte de mention."""
        person = PersonData(
            name="Andrej Karpathy",
            role="AI researcher",
            context="Mentionné pour son travail sur Tesla Autopilot",
        )
        assert person.context == "Mentionné pour son travail sur Tesla Autopilot"


# ---------------------------------------------------------------------------
# TechData
# ---------------------------------------------------------------------------


class TestTechData:
    """Tests pour la dataclass TechData."""

    def test_creation(self):
        """Création d'une technologie."""
        tech = TechData(name="Neo4j", type="database", context="Utilisé comme graph database")
        assert tech.name == "Neo4j"
        assert tech.type == "database"
        assert tech.context == "Utilisé comme graph database"

    def test_types_valides(self):
        """Les types de technologie sont des chaînes libres."""
        for tech_type in ("database", "framework", "library", "platform", "language", "tool"):
            tech = TechData(name="Test", type=tech_type)
            assert tech.type == tech_type


# ---------------------------------------------------------------------------
# TopicData
# ---------------------------------------------------------------------------


class TestTopicData:
    """Tests pour la dataclass TopicData."""

    def test_creation_minimal(self):
        """Un topic avec nom uniquement."""
        topic = TopicData(name="Knowledge Graphs")
        assert topic.name == "Knowledge Graphs"
        assert topic.related == []

    def test_with_related(self):
        """Un topic avec sujets liés."""
        topic = TopicData(
            name="RAG",
            related=["Vector Search", "Embeddings", "LLMs"],
        )
        assert len(topic.related) == 3
        assert "Vector Search" in topic.related


# ---------------------------------------------------------------------------
# ExtractedKnowledge
# ---------------------------------------------------------------------------


class TestExtractedKnowledge:
    """Tests pour la dataclass ExtractedKnowledge."""

    def test_empty(self):
        """ExtractedKnowledge vide est is_empty()."""
        ek = ExtractedKnowledge()
        assert ek.is_empty()
        assert ek.total_items == 0

    def test_with_concepts(self):
        """ExtractedKnowledge avec des concepts n'est pas vide."""
        ek = ExtractedKnowledge(
            concepts=[ConceptData(name="RAG", definition="Retrieval-Augmented Generation")]
        )
        assert not ek.is_empty()
        assert ek.total_items == 1

    def test_total_items_mixed(self):
        """total_items compte tous les types d'entités."""
        ek = ExtractedKnowledge(
            concepts=[ConceptData(name="RAG", definition="RAG")],
            people=[PersonData(name="LeCun", role="Researcher")],
            technologies=[TechData(name="Neo4j", type="database")],
            topics=[TopicData(name="Graphs")],
        )
        assert ek.total_items == 4

    def test_total_items_with_empty_lists(self):
        """Les listes vides ne comptent pas."""
        ek = ExtractedKnowledge(
            concepts=[],
            people=[],
            technologies=[],
            topics=[],
        )
        assert ek.total_items == 0
        assert ek.is_empty()


# ---------------------------------------------------------------------------
# CompilationResult
# ---------------------------------------------------------------------------


class TestCompilationResult:
    """Tests pour la dataclass CompilationResult."""

    def test_success_default(self):
        """Un résultat par défaut est un succès."""
        result = CompilationResult(article_path=Path("/tmp/test.md"))
        assert result.success
        assert result.concepts_created == 0
        assert result.concepts_updated == 0
        assert result.backlinks_created == 0
        assert result.skipped is False
        assert result.errors == []

    def test_success_with_errors(self):
        """Un résultat avec des erreurs n'est pas un succès."""
        result = CompilationResult(
            article_path=Path("/tmp/test.md"),
            errors=["Erreur API", "Timeout"],
        )
        assert not result.success
        assert len(result.errors) == 2

    def test_success_skipped(self):
        """Un résultat skipped n'est pas un succès."""
        result = CompilationResult(
            article_path=Path("/tmp/test.md"),
            skipped=True,
        )
        assert not result.success

    def test_total_wiki_items(self):
        """total_wiki_items = créés + mis à jour."""
        result = CompilationResult(
            article_path=Path("/tmp/test.md"),
            concepts_created=3,
            concepts_updated=2,
        )
        assert result.total_wiki_items == 5

    def test_tokens_default_zero(self):
        """Les tokens sont à 0 par défaut."""
        result = CompilationResult(article_path=Path("/tmp/test.md"))
        assert result.input_tokens == 0
        assert result.output_tokens == 0


# ---------------------------------------------------------------------------
# BatchCompilationResult
# ---------------------------------------------------------------------------


class TestBatchCompilationResult:
    """Tests pour la dataclass BatchCompilationResult."""

    def test_empty_batch(self):
        """Un batch vide a des totaux à 0."""
        batch = BatchCompilationResult()
        assert batch.total_articles == 0
        assert batch.total_compiled == 0
        assert batch.total_skipped == 0
        assert batch.total_concepts_created == 0
        assert batch.total_concepts_updated == 0
        assert batch.total_errors == 0
        assert batch.total_input_tokens == 0
        assert batch.total_output_tokens == 0

    def test_batch_with_results(self):
        """Un batch avec plusieurs résultats agrège correctement."""
        batch = BatchCompilationResult(
            results=[
                CompilationResult(
                    article_path=Path("/tmp/a.md"),
                    concepts_created=2,
                    concepts_updated=1,
                    input_tokens=1000,
                    output_tokens=500,
                ),
                CompilationResult(
                    article_path=Path("/tmp/b.md"),
                    skipped=True,
                ),
                CompilationResult(
                    article_path=Path("/tmp/c.md"),
                    concepts_created=1,
                    errors=["Erreur"],
                    input_tokens=500,
                    output_tokens=200,
                ),
            ]
        )
        assert batch.total_articles == 3
        # Seul a.md est un succès (skipped=False, errors=[])
        assert batch.total_compiled == 1
        assert batch.total_skipped == 1
        assert batch.total_concepts_created == 3
        assert batch.total_concepts_updated == 1
        assert batch.total_errors == 1
        assert batch.total_input_tokens == 1500
        assert batch.total_output_tokens == 700

    def test_summary(self):
        """summary() retourne une chaîne lisible."""
        batch = BatchCompilationResult(
            results=[
                CompilationResult(
                    article_path=Path("/tmp/a.md"),
                    concepts_created=5,
                ),
            ]
        )
        summary = batch.summary()
        assert "1 compilés" in summary
        assert "5 créés" in summary

    def test_batch_all_skipped(self):
        """Un batch où tout est skipped."""
        batch = BatchCompilationResult(
            results=[
                CompilationResult(article_path=Path("/tmp/a.md"), skipped=True),
                CompilationResult(article_path=Path("/tmp/b.md"), skipped=True),
            ]
        )
        assert batch.total_compiled == 0
        assert batch.total_skipped == 2
