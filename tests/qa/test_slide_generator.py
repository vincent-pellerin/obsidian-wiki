"""Tests unitaires pour le générateur de slides Marp.

Couvre :
- _slugify : conversion en slug (identique à report_generator)
- _count_slides : comptage des séparateurs Marp
- SlideGenerator.generate : pipeline complet (mock QAEngine + Gemini)
- SlideGenerator._call_gemini_slides : appel Gemini avec retry (mock)
"""

from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.qa.slide_generator import MARP_HEADER, SlideGenerator, _count_slides, _slugify
from src.qa.models import QueryResult, SlideResult


# ---------------------------------------------------------------------------
# _slugify (identique à report_generator, testé pour couverture)
# ---------------------------------------------------------------------------


class TestSlideSlugify:
    """Tests pour _slugify dans slide_generator."""

    def test_simple_text(self):
        assert _slugify("Knowledge Graphs") == "knowledge-graphs"

    def test_special_characters(self):
        assert _slugify("RAG & LLMs!") == "rag-llms"

    def test_empty_string(self):
        assert _slugify("") == ""


# ---------------------------------------------------------------------------
# _count_slides
# ---------------------------------------------------------------------------


class TestCountSlides:
    """Tests pour _count_slides."""

    def test_single_slide(self):
        content = "# Title\n\nContent here."
        assert _count_slides(content) == 1

    def test_two_slides(self):
        content = "# Slide 1\n\nContent\n\n---\n\n# Slide 2\n\nMore content."
        assert _count_slides(content) == 2

    def test_multiple_slides(self):
        content = "# S1\n\n---\n\n# S2\n\n---\n\n# S3\n\n---\n\n# S4"
        assert _count_slides(content) == 4

    def test_empty_content(self):
        # At minimum 1 slide
        assert _count_slides("") == 1

    def test_frontmatter_dashes_not_counted(self):
        """Les --- du frontmatter Marp sont comptés car la regex les trouve."""
        content = "---\nmarp: true\n---\n\n# Title\n\nContent"
        # _count_slides counts all --- lines, including frontmatter
        result = _count_slides(content)
        assert result >= 2  # frontmatter has 2 dashes + content

    def test_slide_with_code_block_dashes(self):
        """Les --- dans un bloc de code sont aussi comptés."""
        content = "# Title\n\n```\n---\n```\n\n---\n\n# Slide 2"
        result = _count_slides(content)
        assert result >= 2


# ---------------------------------------------------------------------------
# SlideGenerator.generate (mock QAEngine + Gemini)
# ---------------------------------------------------------------------------


class TestSlideGeneratorGenerate:
    """Tests pour SlideGenerator.generate avec QAEngine et Gemini mockés."""

    @pytest.fixture
    def generator(self, vault_path, mock_settings):
        with patch("src.qa.slide_generator.get_settings", return_value=mock_settings):
            gen = SlideGenerator()
            yield gen

    def test_generate_creates_slide_file(self, generator, vault_path):
        """Test qu'un fichier de slides est créé dans 03_OUTPUT/Slides/."""
        mock_query = QueryResult(
            question="Génère une présentation sur : RAG",
            answer="RAG is a technique...",
            sources=["rag", "graphrag"],
        )

        mock_slides = "# RAG\n\nContent\n\n---\n\n# Questions ?\n"

        with patch.object(generator.qa_engine, "query", return_value=mock_query):
            with patch.object(generator, "_call_gemini_slides", return_value=mock_slides):
                result = generator.generate("RAG")

        assert isinstance(result, SlideResult)
        assert result.topic == "RAG"
        assert result.output_path.exists()
        assert result.slides_count >= 1

    def test_generate_contains_marp_header(self, generator, vault_path):
        """Test que le fichier contient l'en-tête Marp."""
        mock_query = QueryResult(
            question="Test",
            answer="Answer",
            sources=[],
        )

        with patch.object(generator.qa_engine, "query", return_value=mock_query):
            with patch.object(generator, "_call_gemini_slides", return_value="# Test\n\nContent"):
                result = generator.generate("Test")

        content = result.output_path.read_text(encoding="utf-8")
        assert "marp: true" in content
        assert content.startswith(MARP_HEADER)

    def test_generate_custom_output_dir(self, generator, vault_path):
        """Test avec un répertoire de sortie personnalisé."""
        custom_dir = vault_path / "custom_slides"
        mock_query = QueryResult(question="Test", answer="Answer", sources=[])

        with patch.object(generator.qa_engine, "query", return_value=mock_query):
            with patch.object(generator, "_call_gemini_slides", return_value="# Test\n\nContent"):
                result = generator.generate("Test", output_dir=custom_dir)

        assert result.output_path.parent == custom_dir
        assert result.output_path.exists()

    def test_generate_filename_contains_date(self, generator, vault_path):
        """Test que le nom de fichier contient la date du jour."""
        mock_query = QueryResult(question="Test", answer="Answer", sources=[])

        with patch.object(generator.qa_engine, "query", return_value=mock_query):
            with patch.object(generator, "_call_gemini_slides", return_value="# Test\n\nContent"):
                result = generator.generate("Test")

        today = date.today().isoformat()
        assert today in result.output_path.name

    def test_generate_gemini_error_creates_fallback(self, generator, vault_path):
        """Test que si Gemini échoue, une slide minimale est créée."""
        mock_query = QueryResult(question="Test", answer="Answer", sources=[])

        with patch.object(generator.qa_engine, "query", return_value=mock_query):
            with patch.object(
                generator, "_call_gemini_slides", side_effect=RuntimeError("API error")
            ):
                result = generator.generate("Test")

        assert result.output_path.exists()
        content = result.output_path.read_text(encoding="utf-8")
        assert "Erreur" in content or "error" in content.lower() or "Questions" in content

    def test_generate_with_sources_in_context(self, generator, vault_path):
        """Test que les sources sont incluses dans le contexte pour Gemini."""
        mock_query = QueryResult(
            question="Test",
            answer="Answer about RAG.",
            sources=["rag", "graphrag"],
        )

        with patch.object(generator.qa_engine, "query", return_value=mock_query) as mock_q:
            with patch.object(
                generator, "_call_gemini_slides", return_value="# RAG\n\nContent"
            ) as mock_slides:
                generator.generate("RAG")

                # Verify _call_gemini_slides was called with context containing sources
                call_args = mock_slides.call_args
                context = call_args[0][1]  # second positional arg = context
                assert "rag" in context or "graphrag" in context


# ---------------------------------------------------------------------------
# SlideGenerator._call_gemini_slides (mock)
# ---------------------------------------------------------------------------


class TestSlideGeneratorCallGeminiSlides:
    """Tests pour SlideGenerator._call_gemini_slides avec mock."""

    @pytest.fixture
    def generator(self, vault_path, mock_settings):
        with patch("src.qa.slide_generator.get_settings", return_value=mock_settings):
            return SlideGenerator()

    def test_call_gemini_slides_returns_content(self, generator):
        """Test que _call_gemini_slides retourne le contenu des slides."""
        mock_response = MagicMock()
        mock_response.text = "# Slide 1\n\nContent\n\n---\n\n# Slide 2\n\nMore"

        with patch("google.genai.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client.models.generate_content.return_value = mock_response
            mock_client_class.return_value = mock_client

            with patch("time.sleep"):
                result = generator._call_gemini_slides("RAG", "Context about RAG")

        assert "Slide 1" in result

    def test_call_gemini_slides_no_api_key_raises(self, generator):
        """Sans clé API, lève RuntimeError."""
        generator._settings = MagicMock()
        generator._settings.get_gemini_api_key.return_value = ""

        with pytest.raises(RuntimeError, match="Clé API"):
            generator._call_gemini_slides("test", "context")

    def test_call_gemini_slides_retry_on_failure(self, generator):
        """Test que _call_gemini_slides retry sur erreur."""
        generator._settings = MagicMock()
        generator._settings.get_gemini_api_key.return_value = "test-key"

        with patch("google.genai.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client.models.generate_content.side_effect = Exception("API error")
            mock_client_class.return_value = mock_client

            with patch("time.sleep"):
                with pytest.raises(RuntimeError, match="tentatives"):
                    generator._call_gemini_slides("test", "context")
