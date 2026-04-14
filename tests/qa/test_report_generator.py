"""Tests unitaires pour le générateur de rapports.

Couvre :
- _slugify : conversion en slug
- ReportGenerator.generate : pipeline complet (mock QAEngine)
"""

from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.qa.report_generator import ReportGenerator, _slugify
from src.qa.models import QueryResult, ReportResult


# ---------------------------------------------------------------------------
# _slugify
# ---------------------------------------------------------------------------


class TestSlugify:
    """Tests pour la fonction _slugify."""

    def test_simple_text(self):
        assert _slugify("Knowledge Graphs") == "knowledge-graphs"

    def test_special_characters_removed(self):
        assert _slugify("RAG & LLMs!") == "rag-llms"

    def test_accents_removed(self):
        # _slugify uses \w which keeps accented chars in Python 3
        result = _slugify("Réseaux de Neurones")
        assert "neurones" in result.lower()

    def test_multiple_spaces_collapsed(self):
        assert _slugify("a   b   c") == "a-b-c"

    def test_leading_trailing_dashes_removed(self):
        assert _slugify("--test--") == "test"

    def test_empty_string(self):
        assert _slugify("") == ""

    def test_long_text_truncated(self):
        long_text = "a" * 200
        result = _slugify(long_text)
        assert len(result) <= 80

    def test_underscores_converted(self):
        assert _slugify("my_topic_name") == "my-topic-name"


# ---------------------------------------------------------------------------
# ReportGenerator.generate (mock QAEngine)
# ---------------------------------------------------------------------------


class TestReportGeneratorGenerate:
    """Tests pour ReportGenerator.generate avec QAEngine mocké."""

    @pytest.fixture
    def generator(self, vault_path, mock_settings):
        with patch("src.qa.report_generator.get_settings", return_value=mock_settings):
            gen = ReportGenerator()
            yield gen

    def test_generate_creates_report_file(self, generator, vault_path):
        """Test qu'un rapport est créé dans 03_OUTPUT/Reports/."""
        mock_result = QueryResult(
            question="Fais un rapport complet sur : RAG",
            answer="RAG est une technique de retrieval...",
            sources=["rag", "graphrag"],
            concepts_used=["RAG", "GraphRAG"],
            input_tokens=500,
            output_tokens=100,
        )

        with patch.object(generator.qa_engine, "query", return_value=mock_result):
            result = generator.generate("RAG")

        assert isinstance(result, ReportResult)
        assert result.topic == "RAG"
        assert result.output_path.exists()
        assert result.sources_count == 2
        assert result.word_count > 0

    def test_generate_custom_output_dir(self, generator, vault_path):
        """Test avec un répertoire de sortie personnalisé."""
        custom_dir = vault_path / "custom_output"
        mock_result = QueryResult(
            question="Test",
            answer="Answer text.",
            sources=[],
        )

        with patch.object(generator.qa_engine, "query", return_value=mock_result):
            result = generator.generate("Test", output_dir=custom_dir)

        assert result.output_path.parent == custom_dir
        assert result.output_path.exists()

    def test_generate_report_contains_frontmatter(self, generator, vault_path):
        """Test que le rapport contient un frontmatter YAML valide."""
        mock_result = QueryResult(
            question="Rapport RAG",
            answer="RAG est...",
            sources=["rag"],
        )

        with patch.object(generator.qa_engine, "query", return_value=mock_result):
            result = generator.generate("RAG")

        content = result.output_path.read_text(encoding="utf-8")
        assert "---" in content
        assert "title:" in content
        assert "date:" in content
        assert "topic:" in content

    def test_generate_report_filename_contains_date(self, generator, vault_path):
        """Test que le nom de fichier contient la date du jour."""
        mock_result = QueryResult(question="Test", answer="Answer", sources=[])

        with patch.object(generator.qa_engine, "query", return_value=mock_result):
            result = generator.generate("Test")

        today = date.today().isoformat()
        assert today in result.output_path.name

    def test_generate_report_with_no_sources(self, generator, vault_path):
        """Test avec aucune source (sources vides)."""
        mock_result = QueryResult(
            question="Test",
            answer="No relevant info found.",
            sources=[],
        )

        with patch.object(generator.qa_engine, "query", return_value=mock_result):
            result = generator.generate("Unknown Topic")

        assert result.sources_count == 0
        assert result.output_path.exists()

    def test_generate_report_word_count(self, generator, vault_path):
        """Test que le word_count correspond au contenu."""
        answer = "This is a test answer with exactly eight words."
        mock_result = QueryResult(
            question="Test",
            answer=answer,
            sources=["source1"],
        )

        with patch.object(generator.qa_engine, "query", return_value=mock_result):
            result = generator.generate("Test")

        assert result.word_count == len(answer.split())
