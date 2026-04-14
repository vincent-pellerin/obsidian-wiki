"""Tests unitaires pour le moteur Q&A.

Couvre :
- QAEngine._search_wiki : recherche via qmd (mock subprocess)
- QAEngine._build_context : agrégation du contenu des fiches
- QAEngine.query : pipeline complet (mock qmd + Gemini)
- QAEngine._call_gemini : appel Gemini avec retry (mock)
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.qa.engine import MAX_FICHE_CHARS, QA_PROMPT, QAEngine
from src.qa.models import QueryResult


# ---------------------------------------------------------------------------
# QAEngine._search_wiki (mock subprocess)
# ---------------------------------------------------------------------------


class TestQAEngineSearchWiki:
    """Tests pour QAEngine._search_wiki avec subprocess mocké."""

    @pytest.fixture
    def engine(self, vault_path, mock_settings):
        with patch("src.qa.engine.get_settings", return_value=mock_settings):
            return QAEngine(model_override="test-model")

    def test_search_returns_paths_from_qmd(self, engine, vault_path):
        """Test que _search_wiki parse correctement les résultats JSON de qmd."""
        # Créer une fiche wiki pour que le chemin existe
        wiki_file = vault_path / "02_WIKI" / "Concepts" / "Rag.md"
        wiki_file.write_text("# RAG\n\nDefinition of RAG.", encoding="utf-8")

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps([{"path": "qmd://wiki/Concepts/Rag.md", "score": 0.95}])

        with patch("subprocess.run", return_value=mock_result):
            paths = engine._search_wiki("RAG", max_results=5)

        assert len(paths) == 1
        assert paths[0].name == "Rag.md"

    def test_search_empty_results(self, engine, vault_path):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "[]"

        with patch("subprocess.run", return_value=mock_result):
            paths = engine._search_wiki("nonexistent", max_results=5)

        assert paths == []

    def test_search_qmd_failure_returns_empty(self, engine, vault_path):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "error"

        with patch("subprocess.run", return_value=mock_result):
            paths = engine._search_wiki("test", max_results=5)

        assert paths == []

    def test_search_timeout_returns_empty(self, engine, vault_path):
        import subprocess

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="qmd", timeout=10)):
            paths = engine._search_wiki("test", max_results=5)

        assert paths == []

    def test_search_invalid_json_returns_empty(self, engine, vault_path):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "not valid json"

        with patch("subprocess.run", return_value=mock_result):
            paths = engine._search_wiki("test", max_results=5)

        assert paths == []

    def test_search_skips_nonexistent_files(self, engine, vault_path):
        """Les chemins retournés par qmd qui n'existent pas sur disque sont ignorés."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(
            [{"path": "qmd://wiki/Concepts/NonExistent.md", "score": 0.5}]
        )

        with patch("subprocess.run", return_value=mock_result):
            paths = engine._search_wiki("test", max_results=5)

        assert paths == []

    def test_search_wiki_dir_missing(self, engine, vault_path):
        """Si le répertoire wiki n'existe pas, retourne une liste vide."""
        import shutil

        shutil.rmtree(vault_path / "02_WIKI")

        paths = engine._search_wiki("test", max_results=5)
        assert paths == []

    def test_search_multiple_results(self, engine, vault_path):
        """Test avec plusieurs résultats qmd."""
        # Créer des fiches wiki
        (vault_path / "02_WIKI" / "Concepts" / "Rag.md").write_text("# RAG", encoding="utf-8")
        (vault_path / "02_WIKI" / "Concepts" / "Graphrag.md").write_text(
            "# GraphRAG", encoding="utf-8"
        )

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(
            [
                {"path": "qmd://wiki/Concepts/Rag.md", "score": 0.95},
                {"path": "qmd://wiki/Concepts/Graphrag.md", "score": 0.85},
            ]
        )

        with patch("subprocess.run", return_value=mock_result):
            paths = engine._search_wiki("RAG", max_results=10)

        assert len(paths) == 2


# ---------------------------------------------------------------------------
# QAEngine._build_context
# ---------------------------------------------------------------------------


class TestQAEngineBuildContext:
    """Tests pour QAEngine._build_context."""

    @pytest.fixture
    def engine(self, vault_path, mock_settings):
        with patch("src.qa.engine.get_settings", return_value=mock_settings):
            return QAEngine(model_override="test-model")

    def test_build_context_reads_files(self, engine, vault_path):
        fiche = vault_path / "02_WIKI" / "Concepts" / "Rag.md"
        fiche.write_text("# RAG\n\nRetrieval-Augmented Generation.", encoding="utf-8")

        context, stems = engine._build_context([fiche])

        assert "RAG" in context
        assert "Rag" in stems

    def test_build_context_truncates_long_files(self, engine, vault_path):
        long_content = "x" * (MAX_FICHE_CHARS + 500)
        fiche = vault_path / "02_WIKI" / "Concepts" / "Long.md"
        fiche.write_text(long_content, encoding="utf-8")

        context, stems = engine._build_context([fiche])

        assert "tronqué" in context

    def test_build_context_multiple_files(self, engine, vault_path):
        f1 = vault_path / "02_WIKI" / "Concepts" / "Rag.md"
        f2 = vault_path / "02_WIKI" / "Concepts" / "Graphrag.md"
        f1.write_text("# RAG", encoding="utf-8")
        f2.write_text("# GraphRAG", encoding="utf-8")

        context, stems = engine._build_context([f1, f2])

        assert len(stems) == 2
        assert "Rag" in stems
        assert "Graphrag" in stems

    def test_build_context_empty_list(self, engine, vault_path):
        context, stems = engine._build_context([])
        assert context == ""
        assert stems == []

    def test_build_context_skips_unreadable_files(self, engine, vault_path):
        nonexistent = vault_path / "02_WIKI" / "Concepts" / "Ghost.md"
        # File doesn't exist — should be skipped gracefully

        context, stems = engine._build_context([nonexistent])

        assert stems == []


# ---------------------------------------------------------------------------
# QAEngine.query (intégration mockée)
# ---------------------------------------------------------------------------


class TestQAEngineQuery:
    """Tests pour QAEngine.query avec qmd et Gemini mockés."""

    @pytest.fixture
    def engine(self, vault_path, mock_settings):
        with patch("src.qa.engine.get_settings", return_value=mock_settings):
            return QAEngine(model_override="test-model")

    def test_query_no_results_returns_message(self, engine, vault_path):
        """Quand qmd ne trouve rien, retourne un message d'absence."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "[]"

        with patch("subprocess.run", return_value=mock_result):
            result = engine.query("What is quantum computing?")

        assert result.question == "What is quantum computing?"
        assert "Aucune fiche" in result.answer or "aucune" in result.answer.lower()
        assert result.sources == []

    def test_query_with_results_calls_gemini(self, engine, vault_path):
        """Quand qmd trouve des résultats, Gemini est appelé pour synthétiser."""
        # Créer une fiche wiki
        fiche = vault_path / "02_WIKI" / "Concepts" / "Rag.md"
        fiche.write_text("# RAG\n\nRetrieval-Augmented Generation.", encoding="utf-8")

        # Mock qmd search
        mock_qmd = MagicMock()
        mock_qmd.returncode = 0
        mock_qmd.stdout = json.dumps([{"path": "qmd://wiki/Concepts/Rag.md", "score": 0.95}])

        # Mock Gemini
        mock_gemini_response = MagicMock()
        mock_gemini_response.text = "RAG est une technique de retrieval..."
        mock_gemini_response.usage_metadata.prompt_token_count = 500
        mock_gemini_response.usage_metadata.candidates_token_count = 100

        with patch("subprocess.run", return_value=mock_qmd):
            with patch(
                "src.qa.engine.QAEngine._call_gemini", return_value=("RAG est...", 500, 100)
            ):
                result = engine.query("Qu'est-ce que RAG ?")

        assert result.question == "Qu'est-ce que RAG ?"
        assert result.answer == "RAG est..."
        assert "Rag" in result.concepts_used
        assert result.input_tokens == 500
        assert result.output_tokens == 100

    def test_query_gemini_error_returns_error(self, engine, vault_path):
        """Quand Gemini échoue, retourne un résultat avec message d'erreur."""
        fiche = vault_path / "02_WIKI" / "Concepts" / "Rag.md"
        fiche.write_text("# RAG", encoding="utf-8")

        mock_qmd = MagicMock()
        mock_qmd.returncode = 0
        mock_qmd.stdout = json.dumps([{"path": "qmd://wiki/Concepts/Rag.md", "score": 0.95}])

        with patch("subprocess.run", return_value=mock_qmd):
            with patch(
                "src.qa.engine.QAEngine._call_gemini", side_effect=RuntimeError("API error")
            ):
                result = engine.query("Qu'est-ce que RAG ?")

        assert "Erreur" in result.answer or "error" in result.answer.lower()
        assert result.concepts_used == ["Rag"]


# ---------------------------------------------------------------------------
# QAEngine._call_gemini (mock)
# ---------------------------------------------------------------------------


class TestQAEngineCallGemini:
    """Tests pour QAEngine._call_gemini avec mock."""

    @pytest.fixture
    def engine(self, vault_path, mock_settings):
        with patch("src.qa.engine.get_settings", return_value=mock_settings):
            return QAEngine(model_override="test-model")

    def test_call_gemini_returns_response(self, engine):
        """Test que _call_gemini retourne bien (text, input_tokens, output_tokens)."""
        mock_response = MagicMock()
        mock_response.text = "RAG est une technique..."
        mock_response.usage_metadata.prompt_token_count = 500
        mock_response.usage_metadata.candidates_token_count = 100

        with patch("google.genai.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client.models.generate_content.return_value = mock_response
            mock_client_class.return_value = mock_client

            text, in_tok, out_tok = engine._call_gemini("What is RAG?", "Context here")

        assert text == "RAG est une technique..."
        assert in_tok == 500
        assert out_tok == 100

    def test_call_gemini_no_api_key_raises(self, engine):
        """Sans clé API, _call_gemini lève RuntimeError."""
        # Override get_gemini_api_key to return empty
        engine._settings = MagicMock()
        engine._settings.get_gemini_api_key.return_value = ""

        with pytest.raises(RuntimeError, match="Clé API"):
            engine._call_gemini("test", "context")

    def test_call_gemini_retry_on_failure(self, engine):
        """Test que _call_gemini retry sur erreur puis lève RuntimeError."""
        engine._settings = MagicMock()
        engine._settings.get_gemini_api_key.return_value = "test-key"

        with patch("google.genai.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client.models.generate_content.side_effect = Exception("API error")
            mock_client_class.return_value = mock_client

            # Patch time.sleep pour accélérer les retries
            with patch("time.sleep"):
                with pytest.raises(RuntimeError, match="tentatives"):
                    engine._call_gemini("test", "context")
