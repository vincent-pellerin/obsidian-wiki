"""Modèles de données partagés pour le système wiki.

Dataclasses utilisées par compiler, concept_manager, linker et indexer.
"""

from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Données extraites par le LLM
# ---------------------------------------------------------------------------


@dataclass
class ConceptData:
    """Concept clé extrait d'un article.

    Attributes:
        name: Nom du concept (ex: "GraphRAG").
        definition: Définition concise en 1-2 phrases.
        context: Comment ce concept est utilisé dans l'article.
        aliases: Noms alternatifs du concept.
    """

    name: str
    definition: str
    context: str = ""
    aliases: list[str] = field(default_factory=list)


@dataclass
class PersonData:
    """Personne mentionnée dans un article.

    Attributes:
        name: Prénom et nom complet.
        role: Titre ou rôle (ex: "chercheur en ML").
        context: Contexte de mention dans l'article.
    """

    name: str
    role: str
    context: str = ""


@dataclass
class TechData:
    """Technologie ou outil mentionné dans un article.

    Attributes:
        name: Nom de la technologie (ex: "Neo4j").
        type: Catégorie (database|framework|library|platform|language|tool).
        context: Usage dans l'article.
    """

    name: str
    type: str
    context: str = ""


@dataclass
class TopicData:
    """Sujet principal d'un article.

    Attributes:
        name: Nom du sujet (ex: "Knowledge Graphs").
        related: Sujets liés.
    """

    name: str
    related: list[str] = field(default_factory=list)


@dataclass
class ExtractedKnowledge:
    """Ensemble des connaissances extraites d'un article par le LLM.

    Attributes:
        concepts: Concepts clés identifiés.
        people: Personnes mentionnées.
        technologies: Technologies et outils mentionnés.
        topics: Sujets principaux.
    """

    concepts: list[ConceptData] = field(default_factory=list)
    people: list[PersonData] = field(default_factory=list)
    technologies: list[TechData] = field(default_factory=list)
    topics: list[TopicData] = field(default_factory=list)

    @property
    def total_items(self) -> int:
        """Nombre total d'éléments extraits."""
        return len(self.concepts) + len(self.people) + len(self.technologies) + len(self.topics)

    def is_empty(self) -> bool:
        """Retourne True si aucun élément n'a été extrait."""
        return self.total_items == 0


# ---------------------------------------------------------------------------
# Résultats de compilation
# ---------------------------------------------------------------------------


@dataclass
class CompilationResult:
    """Résultat de la compilation d'un article.

    Attributes:
        article_path: Chemin du fichier article compilé.
        article_title: Titre de l'article.
        concepts_created: Nombre de nouvelles fiches créées.
        concepts_updated: Nombre de fiches existantes mises à jour.
        backlinks_created: Nombre de backlinks ajoutés.
        skipped: True si l'article a été ignoré (déjà compilé).
        errors: Liste des messages d'erreur rencontrés.
        input_tokens: Tokens en entrée consommés (usage_metadata Gemini).
        output_tokens: Tokens en sortie consommés (usage_metadata Gemini).
    """

    article_path: Path
    article_title: str = ""
    concepts_created: int = 0
    concepts_updated: int = 0
    backlinks_created: int = 0
    skipped: bool = False
    errors: list[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def success(self) -> bool:
        """True si la compilation s'est terminée sans erreur."""
        return len(self.errors) == 0 and not self.skipped

    @property
    def total_wiki_items(self) -> int:
        """Total des fiches créées ou mises à jour."""
        return self.concepts_created + self.concepts_updated


@dataclass
class BatchCompilationResult:
    """Résultat d'une compilation en lot de plusieurs articles.

    Attributes:
        results: Liste des résultats individuels.
    """

    results: list[CompilationResult] = field(default_factory=list)

    @property
    def total_input_tokens(self) -> int:
        """Total des tokens en entrée consommés."""
        return sum(r.input_tokens for r in self.results)

    @property
    def total_output_tokens(self) -> int:
        """Total des tokens en sortie consommés."""
        return sum(r.output_tokens for r in self.results)

    @property
    def total_articles(self) -> int:
        """Nombre d'articles traités."""
        return len(self.results)

    @property
    def total_compiled(self) -> int:
        """Nombre d'articles compilés avec succès."""
        return sum(1 for r in self.results if r.success)

    @property
    def total_skipped(self) -> int:
        """Nombre d'articles ignorés (déjà compilés)."""
        return sum(1 for r in self.results if r.skipped)

    @property
    def total_concepts_created(self) -> int:
        """Total des fiches concepts créées."""
        return sum(r.concepts_created for r in self.results)

    @property
    def total_concepts_updated(self) -> int:
        """Total des fiches concepts mises à jour."""
        return sum(r.concepts_updated for r in self.results)

    @property
    def total_errors(self) -> int:
        """Nombre total d'erreurs rencontrées."""
        return sum(len(r.errors) for r in self.results)

    def summary(self) -> str:
        """Résumé lisible du batch de compilation."""
        return (
            f"Batch: {self.total_compiled}/{self.total_articles} compilés, "
            f"{self.total_skipped} ignorés, "
            f"{self.total_concepts_created} créés + {self.total_concepts_updated} mis à jour, "
            f"{self.total_errors} erreur(s)"
        )
