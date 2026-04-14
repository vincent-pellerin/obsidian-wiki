"""Tests unitaires pour les modèles Q&A.

Couvre :
- QueryResult : defaults, attributs
- ReportResult : defaults, attributs
- SlideResult : defaults, attributs
"""

from pathlib import Path

from src.qa.models import QueryResult, ReportResult, SlideResult


class TestQueryResult:
    """Tests pour QueryResult."""

    def test_defaults(self):
        result = QueryResult(question="What is RAG?", answer="RAG is...")
        assert result.question == "What is RAG?"
        assert result.answer == "RAG is..."
        assert result.sources == []
        assert result.concepts_used == []
        assert result.input_tokens == 0
        assert result.output_tokens == 0

    def test_with_sources(self):
        result = QueryResult(
            question="Test?",
            answer="Answer",
            sources=["rag", "graphrag"],
            concepts_used=["RAG", "GraphRAG"],
            input_tokens=500,
            output_tokens=100,
        )
        assert len(result.sources) == 2
        assert result.input_tokens == 500

    def test_empty_answer(self):
        result = QueryResult(question="Q", answer="")
        assert result.answer == ""


class TestReportResult:
    """Tests pour ReportResult."""

    def test_defaults(self):
        result = ReportResult(
            topic="RAG",
            output_path=Path("/tmp/report.md"),
            word_count=150,
            sources_count=3,
        )
        assert result.topic == "RAG"
        assert result.word_count == 150
        assert result.sources_count == 3

    def test_path_is_path_object(self):
        result = ReportResult(
            topic="Test",
            output_path=Path("/tmp/test.md"),
            word_count=10,
            sources_count=0,
        )
        assert isinstance(result.output_path, Path)


class TestSlideResult:
    """Tests pour SlideResult."""

    def test_defaults(self):
        result = SlideResult(
            topic="Knowledge Graphs",
            output_path=Path("/tmp/slides.md"),
            slides_count=8,
        )
        assert result.topic == "Knowledge Graphs"
        assert result.slides_count == 8

    def test_path_is_path_object(self):
        result = SlideResult(
            topic="Test",
            output_path=Path("/tmp/test.md"),
            slides_count=5,
        )
        assert isinstance(result.output_path, Path)
