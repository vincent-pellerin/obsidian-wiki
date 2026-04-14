"""Modèles de données pour le module Q&A.

Dataclasses utilisées par QAEngine, ReportGenerator et SlideGenerator.
"""

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class QueryResult:
    """Résultat d'une requête Q&A sur le wiki.

    Attributes:
        question: La question posée par l'utilisateur.
        answer: La réponse synthétisée par Gemini.
        sources: Stems des fiches wiki utilisées comme contexte.
        concepts_used: Noms des concepts trouvés lors de la recherche.
        input_tokens: Tokens en entrée consommés (usage_metadata Gemini).
        output_tokens: Tokens en sortie consommés (usage_metadata Gemini).
    """

    question: str
    answer: str
    sources: list[str] = field(default_factory=list)
    concepts_used: list[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class ReportResult:
    """Résultat de la génération d'un rapport Markdown.

    Attributes:
        topic: Sujet du rapport.
        output_path: Chemin du fichier généré.
        word_count: Nombre de mots dans le rapport.
        sources_count: Nombre de sources utilisées.
    """

    topic: str
    output_path: Path
    word_count: int
    sources_count: int


@dataclass
class SlideResult:
    """Résultat de la génération de slides Marp.

    Attributes:
        topic: Sujet de la présentation.
        output_path: Chemin du fichier généré.
        slides_count: Nombre de slides générées.
    """

    topic: str
    output_path: Path
    slides_count: int
