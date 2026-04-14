"""Script de nettoyage du wiki — supprime les fiches et reset les flags de compilation.

Usage :
    uv run python scripts/reset_wiki.py           # dry-run (affiche ce qui serait supprimé)
    uv run python scripts/reset_wiki.py --confirm  # exécute réellement le nettoyage

Ce script :
  1. Supprime toutes les fiches dans 02_WIKI/{Concepts,People,Technologies,Topics}/
  2. Remet à zéro les flags wiki_compiled dans les articles 00_RAW/
  3. Vide le cache .wiki_state.json (section fiches et articles compilés)
  4. Conserve 02_WIKI/Index/ et 02_WIKI/log.md (append d'une entrée de reset)
"""

import argparse
import logging
import sys
from pathlib import Path

import frontmatter

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Sous-dossiers de fiches à vider
WIKI_FICHE_DIRS = ["Concepts", "People", "Technologies", "Topics"]


def parse_args() -> argparse.Namespace:
    """Parse les arguments CLI."""
    parser = argparse.ArgumentParser(
        description="Remet le wiki à zéro (fiches + flags de compilation)."
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Exécute réellement le nettoyage (sans ce flag : dry-run uniquement).",
    )
    parser.add_argument(
        "--vault",
        type=Path,
        default=None,
        help="Chemin du vault Obsidian (défaut : depuis .env / config).",
    )
    parser.add_argument(
        "--skip-raw",
        action="store_true",
        help="Ne pas resetter les flags wiki_compiled dans 00_RAW/ (plus rapide).",
    )
    return parser.parse_args()


def get_vault_path(override: Path | None) -> Path:
    """Résout le chemin du vault depuis l'override ou la config."""
    if override:
        return override
    try:
        from src.config import get_settings

        return Path(get_settings().get_vault_path())
    except Exception as e:
        logger.error(f"Impossible de lire la config : {e}")
        sys.exit(1)


def count_fiches(wiki_root: Path) -> dict[str, int]:
    """Compte les fiches par catégorie."""
    counts = {}
    for subdir in WIKI_FICHE_DIRS:
        d = wiki_root / subdir
        counts[subdir] = len(list(d.glob("*.md"))) if d.exists() else 0
    return counts


def delete_fiches(wiki_root: Path, dry_run: bool) -> int:
    """Supprime toutes les fiches dans les sous-dossiers wiki.

    Args:
        wiki_root: Chemin vers 02_WIKI/.
        dry_run: Si True, affiche sans supprimer.

    Returns:
        Nombre de fiches supprimées (ou qui auraient été supprimées).
    """
    total = 0
    for subdir in WIKI_FICHE_DIRS:
        d = wiki_root / subdir
        if not d.exists():
            continue
        fiches = list(d.glob("*.md"))
        total += len(fiches)
        if dry_run:
            logger.info(f"  [DRY-RUN] {subdir}/: {len(fiches)} fiches à supprimer")
        else:
            for f in fiches:
                f.unlink()
            logger.info(f"  ✅ {subdir}/: {len(fiches)} fiches supprimées")
    return total


def reset_raw_flags(raw_root: Path, dry_run: bool) -> int:
    """Remet à zéro les flags wiki_compiled dans les articles RAW.

    Args:
        raw_root: Chemin vers 00_RAW/.
        dry_run: Si True, affiche sans modifier.

    Returns:
        Nombre d'articles modifiés (ou qui auraient été modifiés).
    """
    articles = list(raw_root.rglob("*.md"))
    modified = 0

    for article_path in articles:
        try:
            post = frontmatter.load(str(article_path))
        except Exception as e:
            logger.warning(f"Lecture impossible : {article_path.name} — {e}")
            continue

        if not post.metadata.get("wiki_compiled"):
            continue

        modified += 1
        if not dry_run:
            # Supprimer les clés de compilation
            for key in ("wiki_compiled", "wiki_compiled_date", "wiki_concepts_count"):
                post.metadata.pop(key, None)
            try:
                article_path.write_text(frontmatter.dumps(post), encoding="utf-8")
            except OSError as e:
                logger.warning(f"Impossible de modifier {article_path.name} : {e}")

    if dry_run:
        logger.info(f"  [DRY-RUN] {modified} articles avec wiki_compiled=True à resetter")
    else:
        logger.info(f"  ✅ {modified} articles remis à zéro")

    return modified


def reset_cache(vault_path: Path, dry_run: bool) -> None:
    """Vide la section fiches et articles compilés du cache .wiki_state.json.

    Args:
        vault_path: Chemin racine du vault.
        dry_run: Si True, affiche sans modifier.
    """
    try:
        from src.wiki.cache import WikiStateCache

        if dry_run:
            logger.info("  [DRY-RUN] Cache .wiki_state.json : section fiches + articles compilés")
            return

        cache = WikiStateCache(vault_path)
        cache.reset_wiki_fiches()
        cache.reset_compiled_articles()
        cache.save()
        logger.info("  ✅ Cache .wiki_state.json remis à zéro")
    except Exception as e:
        logger.warning(f"Impossible de resetter le cache : {e}")


def append_reset_log(wiki_root: Path, stats: dict, dry_run: bool) -> None:
    """Ajoute une entrée de reset dans log.md."""
    if dry_run:
        return
    from datetime import date

    log_path = wiki_root / "log.md"
    today = date.today().isoformat()
    entry = (
        f"\n## [{today}] reset | Nettoyage complet du wiki\n"
        f"- Fiches supprimées : {stats['fiches_deleted']}\n"
        f"- Articles remis à zéro : {stats['articles_reset']}\n"
        f"- Cache vidé : oui\n"
    )
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(entry)
        logger.info("  ✅ Entrée ajoutée dans log.md")
    except OSError as e:
        logger.warning(f"Impossible d'écrire dans log.md : {e}")


def main() -> None:
    """Point d'entrée principal."""
    args = parse_args()
    dry_run = not args.confirm

    vault_path = get_vault_path(args.vault)
    wiki_root = vault_path / "02_WIKI"
    raw_root = vault_path / "00_RAW"

    if not vault_path.exists():
        logger.error(f"Vault introuvable : {vault_path}")
        sys.exit(1)

    # Afficher le résumé avant d'agir
    counts = count_fiches(wiki_root)
    total_fiches = sum(counts.values())

    print()
    print("=" * 60)
    print("RESET WIKI — " + ("DRY-RUN (aucune modification)" if dry_run else "EXÉCUTION RÉELLE"))
    print("=" * 60)
    print(f"Vault : {vault_path}")
    print()
    print("Fiches à supprimer :")
    for subdir, count in counts.items():
        print(f"  {subdir}/: {count} fiches")
    print(f"  TOTAL : {total_fiches} fiches")
    print()

    if not dry_run:
        # Demander confirmation explicite si --confirm passé sans interaction
        print("⚠️  Cette opération est IRRÉVERSIBLE.")
        print("   Les fiches seront supprimées définitivement.")
        print()

    # 1. Supprimer les fiches
    print("1. Suppression des fiches wiki...")
    fiches_deleted = delete_fiches(wiki_root, dry_run)

    # 2. Reset des flags RAW
    if not args.skip_raw:
        print("2. Reset des flags wiki_compiled dans 00_RAW/...")
        articles_reset = reset_raw_flags(raw_root, dry_run)
    else:
        logger.info("  [SKIP] Reset des flags RAW ignoré (--skip-raw)")
        articles_reset = 0

    # 3. Reset du cache
    print("3. Reset du cache .wiki_state.json...")
    reset_cache(vault_path, dry_run)

    # 4. Log
    print("4. Mise à jour de log.md...")
    stats = {"fiches_deleted": fiches_deleted, "articles_reset": articles_reset}
    append_reset_log(wiki_root, stats, dry_run)

    print()
    print("=" * 60)
    if dry_run:
        print("DRY-RUN terminé. Aucune modification effectuée.")
        print("Relancez avec --confirm pour exécuter réellement.")
    else:
        print(
            f"✅ Reset terminé : {fiches_deleted} fiches supprimées, "
            f"{articles_reset} articles remis à zéro."
        )
    print("=" * 60)
    print()


if __name__ == "__main__":
    main()
