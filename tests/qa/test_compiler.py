"""Tests unitaires pour le compilateur wiki.

Couvre :
- _strip_yaml_fences : nettoyage des balises YAML
- _parse_gemini_response : parsing des réponses Gemini
- append_log_entry : journal append-only
- WikiCompiler.compile_article : pipeline complet (mock Gemini)
- WikiCompiler.batch_compile : compilation en lot
- WikiCompiler.get_compilation_stats : statistiques
- WikiCompiler._collect_articles : collecte des articles
- WikiCompiler._mark_compiled : marquage frontmatter
"""

import yaml
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.wiki.compiler import (
    MAX_ARTICLE_CHARS,
    CONCEPT_EXTRACTION_PROMPT,
    WikiCompiler,
    _parse_gemini_response,
    _strip_yaml_fences,
    append_log_entry,
)
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
# _strip_yaml_fences
# ---------------------------------------------------------------------------


class TestStripYamlFences:
    """Tests pour le nettoyage des balises YAML."""

    def test_removes_yaml_fence(self):
        text = "```yaml\nkey: value\n```"
        assert _strip_yaml_fences(text) == "key: value"

    def test_removes_plain_fence(self):
        text = "```\nkey: value\n```"
        assert _strip_yaml_fences(text) == "key: value"

    def test_no_fences_returns_stripped(self):
        text = "  key: value  "
        assert _strip_yaml_fences(text) == "key: value"

    def test_multiline_yaml_with_fence(self):
        content = "```yaml\nconcepts:\n  - name: RAG\n```"
        result = _strip_yaml_fences(content)
        assert result.startswith("concepts:")
        assert result.endswith("name: RAG")

    def test_already_clean_yaml(self):
        text = "concepts:\n  - name: RAG"
        assert _strip_yaml_fences(text) == text

    def test_empty_string(self):
        assert _strip_yaml_fences("") == ""

    def test_only_backticks(self):
        assert _strip_yaml_fences("```") == ""


# ---------------------------------------------------------------------------
# _parse_gemini_response
# ---------------------------------------------------------------------------


class TestParseGeminiResponse:
    """Tests pour le parsing des réponses YAML de Gemini."""

    def test_full_valid_response(self):
        raw = yaml.dump(
            {
                "concepts": [
                    {
                        "name": "RAG",
                        "definition": "Retrieval-Augmented Generation",
                        "context": "Used for QA",
                        "aliases": ["rag"],
                    }
                ],
                "people": [
                    {"name": "LeCun", "role": "Researcher", "context": "Mentioned as pioneer"}
                ],
                "technologies": [
                    {"name": "Neo4j", "type": "database", "context": "Graph database"}
                ],
                "topics": [{"name": "Knowledge Graphs", "related": ["RAG", "NLP"]}],
            }
        )
        knowledge = _parse_gemini_response(raw)
        assert len(knowledge.concepts) == 1
        assert knowledge.concepts[0].name == "RAG"
        assert knowledge.concepts[0].definition == "Retrieval-Augmented Generation"
        assert len(knowledge.people) == 1
        assert knowledge.people[0].name == "LeCun"
        assert len(knowledge.technologies) == 1
        assert knowledge.technologies[0].type == "database"
        assert len(knowledge.topics) == 1
        assert knowledge.topics[0].related == ["RAG", "NLP"]

    def test_response_with_yaml_fences(self):
        raw = "```yaml\nconcepts:\n  - name: Test\n    definition: Def\n```"
        knowledge = _parse_gemini_response(raw)
        assert len(knowledge.concepts) == 1
        assert knowledge.concepts[0].name == "Test"

    def test_empty_concepts_list(self):
        raw = yaml.dump({"concepts": [], "people": [], "technologies": [], "topics": []})
        knowledge = _parse_gemini_response(raw)
        assert knowledge.is_empty()

    def test_invalid_yaml_returns_empty(self):
        knowledge = _parse_gemini_response("not: valid: yaml: [[[")
        assert knowledge.is_empty()

    def test_non_dict_returns_empty(self):
        knowledge = _parse_gemini_response("42")
        assert knowledge.is_empty()

    def test_missing_name_skips_item(self):
        raw = yaml.dump({"concepts": [{"definition": "No name"}]})
        knowledge = _parse_gemini_response(raw)
        assert len(knowledge.concepts) == 0

    def test_missing_optional_fields_use_defaults(self):
        raw = yaml.dump({"concepts": [{"name": "Test"}]})
        knowledge = _parse_gemini_response(raw)
        assert knowledge.concepts[0].definition == ""
        assert knowledge.concepts[0].context == ""
        assert knowledge.concepts[0].aliases == []

    def test_person_missing_role_defaults_empty(self):
        raw = yaml.dump({"people": [{"name": "Ada Lovelace"}]})
        knowledge = _parse_gemini_response(raw)
        assert knowledge.people[0].role == ""

    def test_tech_defaults_to_tool_type(self):
        raw = yaml.dump({"technologies": [{"name": "CustomTool"}]})
        knowledge = _parse_gemini_response(raw)
        assert knowledge.technologies[0].type == "tool"

    def test_topic_empty_related(self):
        raw = yaml.dump({"topics": [{"name": "AI"}]})
        knowledge = _parse_gemini_response(raw)
        assert knowledge.topics[0].related == []

    def test_total_items_counts_all(self):
        raw = yaml.dump(
            {
                "concepts": [{"name": "A", "definition": "d"}],
                "people": [{"name": "B", "role": "r"}],
                "technologies": [{"name": "C", "type": "tool"}],
                "topics": [{"name": "D"}],
            }
        )
        knowledge = _parse_gemini_response(raw)
        assert knowledge.total_items == 4

    def test_null_lists_treated_as_empty(self):
        raw = yaml.dump({"concepts": None, "people": None, "technologies": None, "topics": None})
        knowledge = _parse_gemini_response(raw)
        assert knowledge.is_empty()


# ---------------------------------------------------------------------------
# append_log_entry
# ---------------------------------------------------------------------------


class TestAppendLogEntry:
    """Tests pour le journal log.md."""

    def test_creates_log_file_with_header(self, vault_path):
        log_path = append_log_entry(vault_path, "compile", "Batch 5 articles")
        assert log_path.exists()
        content = log_path.read_text(encoding="utf-8")
        assert "Journal des opérations" in content
        assert "compile" in content
        assert "Batch 5 articles" in content

    def test_appends_to_existing_log(self, vault_path):
        append_log_entry(vault_path, "compile", "First entry")
        append_log_entry(vault_path, "lint", "Second entry")
        log_path = vault_path / "02_WIKI" / "log.md"
        content = log_path.read_text(encoding="utf-8")
        assert "compile" in content
        assert "lint" in content
        # Header appears only once
        assert content.count("Journal des opérations") == 1

    def test_log_with_details(self, vault_path):
        append_log_entry(
            vault_path,
            "compile",
            "Batch 10",
            details={"Fiches créées": 5, "Erreurs": 0},
        )
        log_path = vault_path / "02_WIKI" / "log.md"
        content = log_path.read_text(encoding="utf-8")
        assert "Fiches créées : 5" in content
        assert "Erreurs : 0" in content

    def test_log_entry_contains_today_date(self, vault_path):
        append_log_entry(vault_path, "ingest", "Medium articles")
        log_path = vault_path / "02_WIKI" / "log.md"
        content = log_path.read_text(encoding="utf-8")
        assert date.today().isoformat() in content

    def test_creates_wiki_directory_if_missing(self, vault_path):
        wiki_dir = vault_path / "02_WIKI"
        # Remove the wiki dir to test creation
        import shutil

        shutil.rmtree(wiki_dir)
        log_path = append_log_entry(vault_path, "test", "Dir creation")
        assert log_path.exists()


# ---------------------------------------------------------------------------
# WikiCompiler — compile_article (mock Gemini)
# ---------------------------------------------------------------------------


class TestWikiCompilerCompileArticle:
    """Tests pour WikiCompiler.compile_article avec Gemini mocké."""

    @pytest.fixture
    def compiler(self, vault_path, mock_settings):
        """Crée un WikiCompiler avec Gemini mocké."""
        with patch("src.wiki.compiler._call_gemini") as mock_gemini:
            mock_gemini.return_value = (
                yaml.dump(
                    {
                        "concepts": [
                            {
                                "name": "RAG",
                                "definition": "Retrieval-Augmented Generation",
                                "context": "QA system",
                            }
                        ],
                        "people": [],
                        "technologies": [
                            {"name": "Neo4j", "type": "database", "context": "Graph storage"}
                        ],
                        "topics": [{"name": "Knowledge Graphs", "related": ["RAG"]}],
                    }
                ),
                1000,
                200,
            )
            with patch("src.wiki.compiler.get_settings", return_value=mock_settings):
                comp = WikiCompiler(model_override="test-model")
                comp._call_gemini_mock = mock_gemini
                yield comp

    def test_compile_article_creates_concept_files(self, compiler, vault_path):
        from tests.conftest import make_article

        article_path = make_article(vault_path, content="This article discusses RAG and Neo4j.")
        result = compiler.compile_article(article_path)

        assert result.success
        assert result.concepts_created >= 1
        assert result.input_tokens == 1000
        assert result.output_tokens == 200

    def test_compile_article_skips_already_compiled(self, compiler, vault_path):
        from tests.conftest import make_article

        article_path = make_article(
            vault_path, content="Already done.", extra_metadata={"wiki_compiled": True}
        )
        result = compiler.compile_article(article_path)

        assert result.skipped is True
        assert result.concepts_created == 0

    def test_compile_article_force_recompile(self, compiler, vault_path):
        from tests.conftest import make_article

        article_path = make_article(
            vault_path, content="Force recompile.", extra_metadata={"wiki_compiled": True}
        )
        result = compiler.compile_article(article_path, force=True)

        assert not result.skipped
        assert result.success

    def test_compile_article_empty_content(self, vault_path, mock_settings):
        from tests.conftest import make_article

        article_path = make_article(vault_path, content="   ")
        with patch("src.wiki.compiler.get_settings", return_value=mock_settings):
            compiler = WikiCompiler(model_override="test-model")
        result = compiler.compile_article(article_path)

        assert len(result.errors) > 0
        assert "vide" in result.errors[0].lower() or "empty" in result.errors[0].lower()

    def test_compile_article_gemini_error(self, vault_path, mock_settings):
        from tests.conftest import make_article

        article_path = make_article(vault_path, content="Some content here.")
        with patch("src.wiki.compiler._call_gemini", side_effect=RuntimeError("API error")):
            with patch("src.wiki.compiler.get_settings", return_value=mock_settings):
                compiler = WikiCompiler(model_override="test-model")
            result = compiler.compile_article(article_path)

        assert len(result.errors) > 0
        assert "Gemini" in result.errors[0]

    def test_compile_article_gemini_empty_response(self, vault_path, mock_settings):
        from tests.conftest import make_article

        article_path = make_article(vault_path, content="Some content.")
        with patch("src.wiki.compiler._call_gemini", return_value=("invalid yaml: [[[", 100, 10)):
            with patch("src.wiki.compiler.get_settings", return_value=mock_settings):
                compiler = WikiCompiler(model_override="test-model")
            result = compiler.compile_article(article_path)

        assert len(result.errors) > 0

    def test_compile_article_marks_compiled_in_frontmatter(self, compiler, vault_path):
        from tests.conftest import make_article
        import frontmatter

        article_path = make_article(vault_path, content="Content about RAG.")
        compiler.compile_article(article_path)

        post = frontmatter.load(str(article_path))
        assert post.metadata.get("wiki_compiled") is True
        assert "wiki_compiled_date" in post.metadata

    def test_compile_article_updates_cache(self, compiler, vault_path):
        from tests.conftest import make_article

        article_path = make_article(vault_path, content="Content about RAG.")
        compiler.compile_article(article_path)

        # Verify cache was updated — check article state via public API
        state = compiler.cache.get_article_state(article_path)
        assert state is not None
        assert state.get("wiki_compiled") is True


# ---------------------------------------------------------------------------
# WikiCompiler — batch_compile
# ---------------------------------------------------------------------------


class TestWikiCompilerBatchCompile:
    """Tests pour WikiCompiler.batch_compile."""

    @pytest.fixture
    def compiler(self, vault_path, mock_settings):
        """Crée un WikiCompiler avec Gemini mocké."""
        with patch("src.wiki.compiler._call_gemini") as mock_gemini:
            mock_gemini.return_value = (
                yaml.dump(
                    {
                        "concepts": [
                            {"name": "Test", "definition": "A test concept", "context": "Testing"}
                        ],
                        "people": [],
                        "technologies": [],
                        "topics": [],
                    }
                ),
                500,
                100,
            )
            with patch("src.wiki.compiler.get_settings", return_value=mock_settings):
                comp = WikiCompiler(model_override="test-model")
                yield comp

    def test_batch_compile_processes_multiple_articles(self, compiler, vault_path):
        from tests.conftest import make_article

        make_article(vault_path, filename="article-1", content="Content 1.")
        make_article(vault_path, filename="article-2", content="Content 2.")

        batch = compiler.batch_compile(source="medium")

        assert batch.total_articles == 2
        assert batch.total_compiled >= 1

    def test_batch_compile_with_limit(self, compiler, vault_path):
        from tests.conftest import make_article

        make_article(vault_path, filename="a1", content="Content 1.")
        make_article(vault_path, filename="a2", content="Content 2.")
        make_article(vault_path, filename="a3", content="Content 3.")

        batch = compiler.batch_compile(source="medium", limit=2)
        assert batch.total_articles == 2

    def test_batch_compile_skips_already_compiled(self, compiler, vault_path):
        from tests.conftest import make_article

        make_article(
            vault_path, filename="done", content="Done.", extra_metadata={"wiki_compiled": True}
        )
        make_article(vault_path, filename="new", content="New content.")

        batch = compiler.batch_compile(source="medium")
        assert batch.total_skipped >= 1

    def test_batch_compile_summary(self, compiler, vault_path):
        from tests.conftest import make_article

        make_article(vault_path, filename="article-1", content="Content.")

        batch = compiler.batch_compile(source="medium")
        summary = batch.summary()

        assert "compilés" in summary or "compil" in summary.lower()

    def test_batch_compile_creates_log_entry(self, compiler, vault_path):
        from tests.conftest import make_article

        make_article(vault_path, filename="article-1", content="Content.")
        compiler.batch_compile(source="medium")

        log_path = vault_path / "02_WIKI" / "log.md"
        assert log_path.exists()
        content = log_path.read_text(encoding="utf-8")
        assert "compile" in content


# ---------------------------------------------------------------------------
# WikiCompiler — get_compilation_stats
# ---------------------------------------------------------------------------


class TestWikiCompilerStats:
    """Tests pour WikiCompiler.get_compilation_stats."""

    @pytest.fixture
    def compiler(self, vault_path, mock_settings):
        with patch("src.wiki.compiler.get_settings", return_value=mock_settings):
            return WikiCompiler(model_override="test-model")

    def test_stats_empty_vault(self, compiler, vault_path):
        stats = compiler.get_compilation_stats()
        assert stats["total_raw"] == 0
        assert stats["total_compiled"] == 0
        assert stats["pending_count"] == 0

    def test_stats_with_articles(self, compiler, vault_path):
        from tests.conftest import make_article

        make_article(vault_path, filename="a1", content="Content.")
        make_article(vault_path, filename="a2", content="More content.")

        stats = compiler.get_compilation_stats()
        assert stats["total_raw"] == 2

    def test_stats_with_wiki_fiches(self, compiler, vault_path):
        from tests.conftest import make_concept

        make_concept(vault_path, name="RAG")
        make_concept(vault_path, name="GraphRAG")

        stats = compiler.get_compilation_stats()
        assert stats["total_wiki_fiches"] >= 2


# ---------------------------------------------------------------------------
# WikiCompiler — _collect_articles
# ---------------------------------------------------------------------------


class TestWikiCompilerCollectArticles:
    """Tests pour WikiCompiler._collect_articles."""

    @pytest.fixture
    def compiler(self, vault_path, mock_settings):
        with patch("src.wiki.compiler.get_settings", return_value=mock_settings):
            return WikiCompiler(model_override="test-model")

    def test_collect_medium_articles(self, compiler, vault_path):
        from tests.conftest import make_article

        make_article(vault_path, source="medium", filename="m1", content="Medium article.")
        make_article(vault_path, source="substack", filename="s1", content="Substack article.")

        articles = compiler._collect_articles("medium")
        assert all("medium" in str(a) for a in articles)

    def test_collect_all_sources(self, compiler, vault_path):
        from tests.conftest import make_article

        make_article(vault_path, source="medium", filename="m1", content="Medium.")
        make_article(vault_path, source="substack", filename="s1", content="Substack.")

        articles = compiler._collect_articles("all")
        assert len(articles) >= 2

    def test_collect_empty_directory(self, compiler, vault_path):
        articles = compiler._collect_articles("medium")
        assert articles == []


# ---------------------------------------------------------------------------
# WikiCompiler — _mark_compiled
# ---------------------------------------------------------------------------


class TestWikiCompilerMarkCompiled:
    """Tests pour WikiCompiler._mark_compiled."""

    @pytest.fixture
    def compiler(self, vault_path, mock_settings):
        with patch("src.wiki.compiler.get_settings", return_value=mock_settings):
            return WikiCompiler(model_override="test-model")

    def test_mark_compiled_sets_frontmatter(self, compiler, vault_path):
        import frontmatter

        article_path = vault_path / "00_RAW" / "articles" / "medium" / "test.md"
        article_path.parent.mkdir(parents=True, exist_ok=True)
        post = frontmatter.Post("Content here.", metadata={"title": "Test"})
        article_path.write_text(frontmatter.dumps(post), encoding="utf-8")

        knowledge = ExtractedKnowledge(
            concepts=[ConceptData(name="Test", definition="A test")],
        )
        compiler._mark_compiled(article_path, post, knowledge)

        updated = frontmatter.load(str(article_path))
        assert updated.metadata["wiki_compiled"] is True
        assert "wiki_compiled_date" in updated.metadata
        assert updated.metadata["wiki_concepts_count"] == 1


# ---------------------------------------------------------------------------
# BatchCompilationResult — propriétés
# ---------------------------------------------------------------------------


class TestBatchCompilationResult:
    """Tests pour les propriétés de BatchCompilationResult."""

    def test_total_input_tokens(self):
        batch = BatchCompilationResult(
            results=[
                CompilationResult(article_path=Path("a.md"), input_tokens=100, output_tokens=50),
                CompilationResult(article_path=Path("b.md"), input_tokens=200, output_tokens=100),
            ]
        )
        assert batch.total_input_tokens == 300
        assert batch.total_output_tokens == 150

    def test_total_compiled_counts_success_only(self):
        batch = BatchCompilationResult(
            results=[
                CompilationResult(article_path=Path("a.md")),
                CompilationResult(article_path=Path("b.md"), errors=["error"]),
                CompilationResult(article_path=Path("c.md"), skipped=True),
            ]
        )
        assert batch.total_compiled == 1  # Only the first one is success

    def test_total_skipped(self):
        batch = BatchCompilationResult(
            results=[
                CompilationResult(article_path=Path("a.md"), skipped=True),
                CompilationResult(article_path=Path("b.md"), skipped=True),
                CompilationResult(article_path=Path("c.md")),
            ]
        )
        assert batch.total_skipped == 2

    def test_total_concepts_created_and_updated(self):
        batch = BatchCompilationResult(
            results=[
                CompilationResult(
                    article_path=Path("a.md"), concepts_created=3, concepts_updated=1
                ),
                CompilationResult(
                    article_path=Path("b.md"), concepts_created=2, concepts_updated=4
                ),
            ]
        )
        assert batch.total_concepts_created == 5
        assert batch.total_concepts_updated == 5

    def test_total_errors(self):
        batch = BatchCompilationResult(
            results=[
                CompilationResult(article_path=Path("a.md"), errors=["e1", "e2"]),
                CompilationResult(article_path=Path("b.md"), errors=["e3"]),
            ]
        )
        assert batch.total_errors == 3

    def test_summary_format(self):
        batch = BatchCompilationResult(
            results=[
                CompilationResult(article_path=Path("a.md"), concepts_created=2),
            ]
        )
        summary = batch.summary()
        assert "compil" in summary.lower()
