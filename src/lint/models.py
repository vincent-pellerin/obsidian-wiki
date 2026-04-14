"""Modèles de données pour le module lint (health checker).

Dataclasses utilisées par HealthChecker et Enricher.
"""

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class BrokenLink:
    """Lien wiki cassé détecté dans une fiche.

    Attributes:
        source_file: Fichier contenant le lien cassé.
        link_target: Cible du lien [[target]].
        line_number: Numéro de ligne du lien dans le fichier source.
    """

    source_file: Path
    link_target: str
    line_number: int = 0


@dataclass
class OrphanedConcept:
    """Fiche wiki sans source (non référencée par aucun article RAW).

    Attributes:
        path: Chemin de la fiche orpheline.
        title: Titre de la fiche.
        wiki_type: Type de fiche (concept, person, technology, topic).
    """

    path: Path
    title: str
    wiki_type: str


@dataclass
class DuplicateGroup:
    """Groupe de fiches wiki potentiellement dupliquées.

    Attributes:
        canonical: Fiche la plus complète (source_count le plus élevé).
        duplicates: Fiches considérées comme doublons du canonical.
    """

    canonical: Path
    duplicates: list[Path] = field(default_factory=list)


@dataclass
class MissingDefinition:
    """Fiche wiki dont la section principale est vide ou incomplète.

    Attributes:
        path: Chemin de la fiche concernée.
        title: Titre de la fiche.
        section: Quelle section est vide (Définition, Biographie, etc.).
    """

    path: Path
    title: str
    section: str


@dataclass
class HealthReport:
    """Rapport de santé complet du wiki.

    Attributes:
        broken_links: Liens wiki cassés détectés.
        orphaned_concepts: Fiches sans source.
        duplicate_groups: Groupes de fiches potentiellement dupliquées.
        missing_definitions: Fiches avec sections vides.
        total_wiki_fiches: Nombre total de fiches dans le wiki.
        score: Score de santé 0-100.
    """

    broken_links: list[BrokenLink] = field(default_factory=list)
    orphaned_concepts: list[OrphanedConcept] = field(default_factory=list)
    duplicate_groups: list[DuplicateGroup] = field(default_factory=list)
    missing_definitions: list[MissingDefinition] = field(default_factory=list)
    total_wiki_fiches: int = 0
    score: int = 100

    @property
    def is_healthy(self) -> bool:
        """True si le score de santé est >= 80."""
        return self.score >= 80

    def summary(self) -> str:
        """Retourne un résumé lisible du rapport de santé.

        Returns:
            Chaîne de caractères résumant les problèmes détectés.
        """
        status = (
            "✅ Sain" if self.is_healthy else ("⚠️  Dégradé" if self.score >= 50 else "❌ Critique")
        )
        lines = [
            f"Score : {self.score}/100 — {status}",
            f"Fiches wiki : {self.total_wiki_fiches}",
            f"Liens cassés : {len(self.broken_links)}",
            f"Concepts orphelins : {len(self.orphaned_concepts)}",
            f"Groupes de doublons : {len(self.duplicate_groups)}",
            f"Définitions manquantes : {len(self.missing_definitions)}",
        ]
        return "\n".join(lines)
