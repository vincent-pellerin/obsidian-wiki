"""Health checker — analyse la qualité et cohérence du wiki.

Détecte les liens cassés, concepts orphelins, doublons et définitions
manquantes pour produire un score de santé global.
"""

import logging
import re
from pathlib import Path

import frontmatter

from src.config import get_settings
from src.lint.models import (
    BrokenLink,
    DuplicateGroup,
    HealthReport,
    MissingDefinition,
    OrphanedConcept,
)

logger = logging.getLogger(__name__)

# Regex pour extraire les wikilinks [[target]] ou [[target|alias]]
WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]")

# Placeholders indiquant une section vide
EMPTY_PLACEHOLDERS = {"_à compléter_", "_a completer_", "à compléter", "a completer"}

# Sections principales attendues selon le type de fiche
MAIN_SECTIONS: dict[str, str] = {
    "concept": "Définition",
    "person": "Biographie",
    "technology": "Description",
    "topic": "Description",
}


def _normalize_name(name: str) -> str:
    """Normalise un nom de fiche pour la détection de doublons.

    Args:
        name: Nom brut (stem du fichier ou titre).

    Returns:
        Nom normalisé en minuscules sans caractères spéciaux.
    """
    normalized = name.lower()
    normalized = re.sub(r"[^\w\s]", "", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


class HealthChecker:
    """Analyse la qualité et cohérence du wiki Obsidian.

    Effectue plusieurs vérifications : liens cassés, concepts orphelins,
    doublons potentiels et définitions manquantes.

    Attributes:
        vault_path: Chemin racine du vault Obsidian.
        wiki_path: Chemin du répertoire 02_WIKI/.
    """

    def __init__(self) -> None:
        """Initialise le health checker avec la configuration courante."""
        settings = get_settings()
        self.vault_path = Path(settings.get_vault_path())
        self.wiki_path = self.vault_path / "02_WIKI"

    def run_full_check(self) -> HealthReport:
        """Lance tous les checks et retourne un HealthReport avec score.

        Returns:
            HealthReport complet avec score calculé.
        """
        logger.info("Démarrage du health check complet...")

        # Compter les fiches totales
        total_fiches = len(list(self.wiki_path.rglob("*.md"))) if self.wiki_path.exists() else 0
        logger.info(f"Fiches wiki trouvées : {total_fiches}")

        # Lancer tous les checks
        broken = self.check_broken_links()
        logger.info(f"Liens cassés : {len(broken)}")

        orphaned = self.check_orphaned_concepts()
        logger.info(f"Concepts orphelins : {len(orphaned)}")

        duplicates = self.check_duplicate_concepts()
        logger.info(f"Groupes de doublons : {len(duplicates)}")

        missing = self.check_missing_definitions()
        logger.info(f"Définitions manquantes : {len(missing)}")

        score = self._calculate_score(broken, orphaned, duplicates, missing, total_fiches)

        return HealthReport(
            broken_links=broken,
            orphaned_concepts=orphaned,
            duplicate_groups=duplicates,
            missing_definitions=missing,
            total_wiki_fiches=total_fiches,
            score=score,
        )

    def check_broken_links(self) -> list[BrokenLink]:
        """Trouve tous les [[wikilinks]] dont la cible n'existe pas.

        Algorithme :
        1. Parcourir tous les .md dans 02_WIKI/
        2. Extraire tous les [[liens]] avec regex
        3. Vérifier si le fichier cible existe dans tout le vault
        4. Retourner les liens dont la cible est introuvable

        Returns:
            Liste des liens cassés détectés.
        """
        if not self.wiki_path.exists():
            logger.warning(f"Répertoire wiki introuvable : {self.wiki_path}")
            return []

        # Construire l'index de tous les fichiers .md du vault (stem → path)
        all_md_stems: set[str] = set()
        for md_file in self.vault_path.rglob("*.md"):
            all_md_stems.add(md_file.stem.lower())

        broken_links: list[BrokenLink] = []

        for md_file in self.wiki_path.rglob("*.md"):
            try:
                lines = md_file.read_text(encoding="utf-8").splitlines()
            except OSError as e:
                logger.debug(f"Impossible de lire {md_file.name} : {e}")
                continue

            for line_num, line in enumerate(lines, 1):
                for match in WIKILINK_RE.finditer(line):
                    target = match.group(1).strip()
                    # Ignorer les liens vides ou les ancres pures
                    if not target or target.startswith("#"):
                        continue
                    # Normaliser : prendre seulement le nom de fichier (sans chemin)
                    target_stem = Path(target).stem.lower()
                    if target_stem not in all_md_stems:
                        broken_links.append(
                            BrokenLink(
                                source_file=md_file,
                                link_target=target,
                                line_number=line_num,
                            )
                        )

        return broken_links

    def check_orphaned_concepts(self) -> list[OrphanedConcept]:
        """Trouve les fiches wiki sans source (source_count == 0 ou absent).

        Vérifie le frontmatter de chaque fiche pour le champ source_count.
        Une fiche est orpheline si source_count est absent ou égal à 0.

        Returns:
            Liste des concepts orphelins.
        """
        if not self.wiki_path.exists():
            return []

        orphaned: list[OrphanedConcept] = []

        for md_file in self.wiki_path.rglob("*.md"):
            # Ignorer l'index maître
            if md_file.stem.startswith("000_"):
                continue
            try:
                post = frontmatter.load(str(md_file))
                source_count = post.metadata.get("source_count", 0)
                if not source_count or int(source_count) == 0:
                    wiki_type = str(post.metadata.get("type", "concept"))
                    title = str(post.metadata.get("title", md_file.stem))
                    orphaned.append(
                        OrphanedConcept(
                            path=md_file,
                            title=title,
                            wiki_type=wiki_type,
                        )
                    )
            except Exception as e:
                logger.debug(f"Erreur lecture frontmatter {md_file.name} : {e}")

        return orphaned

    def check_duplicate_concepts(self) -> list[DuplicateGroup]:
        """Détecte les fiches dont les noms sont très similaires.

        Algorithme optimisé O(n) :
        1. Normaliser les noms (lowercase, supprimer caractères spéciaux)
        2. Phase 1 — Hash grouping : grouper par nom normalisé identique (O(n))
        3. Phase 2 — Préfixe : trier puis scan linéaire pour trouver les préfixes (O(n log n))

        Returns:
            Liste des groupes de doublons potentiels.
        """
        if not self.wiki_path.exists():
            return []

        # Collecter toutes les fiches avec leur nom normalisé
        fiches: list[tuple[str, Path]] = []
        for md_file in self.wiki_path.rglob("*.md"):
            if md_file.stem.startswith("000_"):
                continue
            normalized = _normalize_name(md_file.stem)
            if normalized and len(normalized) >= 5:
                fiches.append((normalized, md_file))

        # Phase 1 — Hash grouping : noms normalisés identiques → O(n)
        exact_groups: dict[str, list[Path]] = {}
        for norm, path in fiches:
            exact_groups.setdefault(norm, []).append(path)

        # Phase 2 — Préfixe : tri + scan linéaire → O(n log n)
        sorted_fiches = sorted(fiches, key=lambda x: x[0])
        prefix_groups: dict[str, list[Path]] = {}

        for idx in range(len(sorted_fiches) - 1):
            norm_i, path_i = sorted_fiches[idx]
            # Regarder les suivants tant qu'ils sont préfixés par norm_i
            for jdx in range(idx + 1, len(sorted_fiches)):
                norm_j, path_j = sorted_fiches[jdx]
                if norm_j.startswith(norm_i) and norm_i != norm_j:
                    key = norm_i
                    if key not in prefix_groups:
                        prefix_groups[key] = []
                    if path_i not in prefix_groups[key]:
                        prefix_groups[key].append(path_i)
                    if path_j not in prefix_groups[key]:
                        prefix_groups[key].append(path_j)
                else:
                    # Trié : dès qu'un suivant n'est plus préfixé, on arrête
                    break

        # Fusionner les deux sources de groupes
        merged: dict[str, list[Path]] = {}

        for key, paths in exact_groups.items():
            if len(paths) >= 2:
                merged[key] = list(paths)

        for key, paths in prefix_groups.items():
            if key in merged:
                for p in paths:
                    if p not in merged[key]:
                        merged[key].append(p)
            else:
                merged[key] = list(paths)

        # Construire les DuplicateGroup en choisissant le canonical
        duplicate_groups: list[DuplicateGroup] = []
        for paths in merged.values():
            if len(paths) < 2:
                continue
            canonical = self._pick_canonical(paths)
            duplicates = [p for p in paths if p != canonical]
            duplicate_groups.append(DuplicateGroup(canonical=canonical, duplicates=duplicates))

        return duplicate_groups

    def check_missing_definitions(self) -> list[MissingDefinition]:
        """Trouve les fiches dont la section principale est vide.

        Détecte les sections vides ou contenant seulement le placeholder
        "_À compléter_".

        Returns:
            Liste des fiches avec définitions manquantes.
        """
        if not self.wiki_path.exists():
            return []

        missing: list[MissingDefinition] = []

        for md_file in self.wiki_path.rglob("*.md"):
            if md_file.stem.startswith("000_"):
                continue
            try:
                post = frontmatter.load(str(md_file))
                wiki_type = str(post.metadata.get("type", "concept"))
                title = str(post.metadata.get("title", md_file.stem))
                section_name = MAIN_SECTIONS.get(wiki_type, "Définition")

                content = post.content or ""
                if self._is_section_empty(content, section_name):
                    missing.append(
                        MissingDefinition(
                            path=md_file,
                            title=title,
                            section=section_name,
                        )
                    )
            except Exception as e:
                logger.debug(f"Erreur lecture {md_file.name} : {e}")

        return missing

    def _calculate_score(
        self,
        broken: list,
        orphaned: list,
        duplicates: list,
        missing: list,
        total: int,
    ) -> int:
        """Calcule un score de santé 0-100.

        Pénalités :
        - -5 par lien cassé (max -30)
        - -3 par concept orphelin (max -20)
        - -5 par groupe de doublons (max -20)
        - -2 par définition manquante (max -20)

        Args:
            broken: Liste des liens cassés.
            orphaned: Liste des concepts orphelins.
            duplicates: Liste des groupes de doublons.
            missing: Liste des définitions manquantes.
            total: Nombre total de fiches wiki.

        Returns:
            Score entre 0 et 100.
        """
        score = 100

        # Pénalités avec plafonds
        broken_penalty = min(len(broken) * 5, 30)
        orphaned_penalty = min(len(orphaned) * 3, 20)
        duplicates_penalty = min(len(duplicates) * 5, 20)
        missing_penalty = min(len(missing) * 2, 20)

        score -= broken_penalty + orphaned_penalty + duplicates_penalty + missing_penalty

        return max(0, score)

    def _pick_canonical(self, paths: list[Path]) -> Path:
        """Choisit la fiche canonique parmi un groupe de doublons.

        Sélectionne la fiche avec le source_count le plus élevé.
        En cas d'égalité, prend la plus longue (plus de contenu).

        Args:
            paths: Liste des chemins de fiches candidates.

        Returns:
            Chemin de la fiche canonique.
        """
        best_path = paths[0]
        best_score = -1

        for path in paths:
            try:
                post = frontmatter.load(str(path))
                source_count = int(post.metadata.get("source_count", 0))
                content_len = len(post.content or "")
                # Score composite : source_count prioritaire, puis longueur
                composite = source_count * 10000 + content_len
                if composite > best_score:
                    best_score = composite
                    best_path = path
            except Exception:
                pass

        return best_path

    def _is_section_empty(self, content: str, section_name: str) -> bool:
        """Vérifie si une section est vide ou contient un placeholder.

        Args:
            content: Contenu markdown de la fiche (sans frontmatter).
            section_name: Nom de la section à vérifier.

        Returns:
            True si la section est vide ou contient un placeholder.
        """
        # Chercher la section dans le contenu
        section_pattern = re.compile(
            rf"^##\s+{re.escape(section_name)}\s*$",
            re.MULTILINE | re.IGNORECASE,
        )
        match = section_pattern.search(content)
        if not match:
            # Section absente = considérée comme manquante
            return True

        # Extraire le contenu de la section (jusqu'à la prochaine section ##)
        start = match.end()
        next_section = re.search(r"^##\s+", content[start:], re.MULTILINE)
        section_content = (
            content[start : start + next_section.start()] if next_section else content[start:]
        )

        # Vérifier si vide ou placeholder
        stripped = section_content.strip().lower()
        if not stripped:
            return True
        return stripped in EMPTY_PLACEHOLDERS
