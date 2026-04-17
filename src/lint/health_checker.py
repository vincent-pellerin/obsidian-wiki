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
                    # Remplacer les espaces par des underscores pour matcher
                    # les fichiers nommés avec underscores (ex: [[Knowledge Graph]] → Knowledge_Graph.md)
                    target_stem = Path(target).stem.lower().replace(" ", "_")
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
        """Détecte les fiches dont les noms normalisés sont identiques.

        Algorithme O(n) — hash grouping uniquement :
        1. Normaliser les noms (lowercase, supprimer ponctuation/tirets/underscores)
        2. Grouper les fiches par nom normalisé identique
        3. Tout groupe avec 2+ fiches = doublon confirmé

        La détection par préfixe a été volontairement supprimée car elle génère
        trop de faux positifs (ex: "Abstraction" ≠ "Abstraction_Logicielle").

        Returns:
            Liste des groupes de doublons confirmés.
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

        # Hash grouping : noms normalisés identiques → O(n)
        exact_groups: dict[str, list[Path]] = {}
        for norm, path in fiches:
            exact_groups.setdefault(norm, []).append(path)

        # Construire les DuplicateGroup en choisissant le canonical
        duplicate_groups: list[DuplicateGroup] = []
        for paths in exact_groups.values():
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

    def merge_duplicates(self, groups: list[DuplicateGroup]) -> int:
        """Fusionne les groupes de doublons dans leur fiche canonique.

        Pour chaque groupe :
        1. Consolide aliases, source_count, sources et concepts liés dans le canonical
        2. Redirige tous les wikilinks du vault vers le canonical
        3. Supprime les fichiers doublons

        Args:
            groups: Liste des groupes de doublons à fusionner.

        Returns:
            Nombre de fichiers doublons supprimés.
        """
        deleted = 0

        for group in groups:
            canonical_path = group.canonical
            try:
                canonical_post = frontmatter.load(str(canonical_path))
            except Exception as e:
                logger.warning(f"Impossible de lire le canonical {canonical_path.name} : {e}")
                continue

            # Accumuler les données des doublons
            merged_aliases: list[str] = list(canonical_post.metadata.get("aliases", []) or [])
            merged_source_count: int = int(canonical_post.metadata.get("source_count", 0) or 0)
            merged_sources: list[str] = self._extract_section_items(
                canonical_post.content or "", "Sources mentionnant ce concept"
            )
            merged_related: list[str] = self._extract_section_items(
                canonical_post.content or "", "Concepts liés"
            )

            for dup_path in group.duplicates:
                try:
                    dup_post = frontmatter.load(str(dup_path))
                except Exception as e:
                    logger.warning(f"Impossible de lire le doublon {dup_path.name} : {e}")
                    continue

                # Fusionner aliases
                for alias in dup_post.metadata.get("aliases", []) or []:
                    if alias not in merged_aliases:
                        merged_aliases.append(alias)
                # Ajouter le stem du doublon comme alias s'il n'y est pas
                dup_title = str(dup_post.metadata.get("title", dup_path.stem))
                if dup_title not in merged_aliases:
                    merged_aliases.append(dup_title)

                # Additionner source_count
                merged_source_count += int(dup_post.metadata.get("source_count", 0) or 0)

                # Fusionner sources
                for item in self._extract_section_items(
                    dup_post.content or "", "Sources mentionnant ce concept"
                ):
                    if item not in merged_sources:
                        merged_sources.append(item)

                # Fusionner concepts liés
                for item in self._extract_section_items(dup_post.content or "", "Concepts liés"):
                    if item not in merged_related:
                        merged_related.append(item)

            # Mettre à jour le frontmatter du canonical
            canonical_post.metadata["aliases"] = merged_aliases
            canonical_post.metadata["source_count"] = merged_source_count
            canonical_post.metadata["updated"] = str(__import__("datetime").date.today())

            # Reconstruire le contenu avec les sections fusionnées
            new_content = self._rebuild_content(
                canonical_post.content or "",
                merged_sources,
                merged_related,
            )
            canonical_post.content = new_content

            # Écrire le canonical mis à jour
            try:
                canonical_path.write_text(frontmatter.dumps(canonical_post), encoding="utf-8")
                logger.info(f"Canonical mis à jour : {canonical_path.name}")
            except Exception as e:
                logger.warning(f"Erreur écriture canonical {canonical_path.name} : {e}")
                continue

            # Rediriger les wikilinks dans tout le vault
            for dup_path in group.duplicates:
                self._redirect_wikilinks(dup_path.stem, canonical_path.stem)

            # Supprimer les doublons
            for dup_path in group.duplicates:
                try:
                    dup_path.unlink()
                    deleted += 1
                    logger.info(f"Doublon supprimé : {dup_path.name}")
                except Exception as e:
                    logger.warning(f"Erreur suppression {dup_path.name} : {e}")

        return deleted

    def _extract_section_items(self, content: str, section_name: str) -> list[str]:
        """Extrait les items (lignes non vides) d'une section markdown.

        Args:
            content: Contenu markdown de la fiche.
            section_name: Nom de la section à extraire.

        Returns:
            Liste des lignes non vides de la section.
        """
        pattern = re.compile(
            rf"^##\s+{re.escape(section_name)}\s*$",
            re.MULTILINE | re.IGNORECASE,
        )
        match = pattern.search(content)
        if not match:
            return []

        start = match.end()
        next_section = re.search(r"^##\s+", content[start:], re.MULTILINE)
        section_content = (
            content[start : start + next_section.start()] if next_section else content[start:]
        )
        return [line.strip() for line in section_content.splitlines() if line.strip()]

    def _rebuild_content(
        self,
        content: str,
        merged_sources: list[str],
        merged_related: list[str],
    ) -> str:
        """Reconstruit le contenu en remplaçant les sections fusionnées.

        Args:
            content: Contenu original du canonical.
            merged_sources: Items fusionnés pour "Sources mentionnant ce concept".
            merged_related: Items fusionnés pour "Concepts liés".

        Returns:
            Contenu reconstruit avec les sections mises à jour.
        """

        def replace_section(text: str, section_name: str, new_items: list[str]) -> str:
            pattern = re.compile(
                rf"(^##\s+{re.escape(section_name)}\s*$)(.*?)(?=^##\s+|\Z)",
                re.MULTILINE | re.IGNORECASE | re.DOTALL,
            )
            new_body = "\n" + "\n".join(new_items) + "\n\n"
            result = pattern.sub(lambda m: m.group(1) + new_body, text)
            # Si la section n'existait pas, l'ajouter en fin
            if result == text and new_items:
                result = text.rstrip() + f"\n\n## {section_name}\n" + "\n".join(new_items) + "\n"
            return result

        content = replace_section(content, "Sources mentionnant ce concept", merged_sources)
        content = replace_section(content, "Concepts liés", merged_related)
        return content

    def _redirect_wikilinks(self, old_stem: str, new_stem: str) -> int:
        """Remplace tous les [[old_stem]] par [[new_stem]] dans le vault.

        Args:
            old_stem: Stem du fichier doublon (sans extension).
            new_stem: Stem du fichier canonical (sans extension).

        Returns:
            Nombre de fichiers modifiés.
        """
        modified = 0
        # Patterns à remplacer : [[old_stem]] et [[old_stem|alias]]
        pattern = re.compile(
            rf"\[\[{re.escape(old_stem)}(\|[^\]]*)?\]\]",
            re.IGNORECASE,
        )

        for md_file in self.vault_path.rglob("*.md"):
            if md_file == self.vault_path / "02_WIKI" / md_file.relative_to(self.vault_path):
                pass  # inclure les fiches wiki aussi
            try:
                original = md_file.read_text(encoding="utf-8")
                updated = pattern.sub(lambda m: f"[[{new_stem}{m.group(1) or ''}]]", original)
                if updated != original:
                    md_file.write_text(updated, encoding="utf-8")
                    modified += 1
            except OSError:
                pass

        logger.debug(f"Wikilinks redirigés : [[{old_stem}]] → [[{new_stem}]] ({modified} fichiers)")
        return modified

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
