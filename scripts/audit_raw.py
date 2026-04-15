"""Audit des articles RAW — détecte les fichiers polluants sans appeler Gemini.

Catégories détectées :
  - invalid     : CAPTCHA, 403, page vide (< 500 chars)
  - newsletter  : contenu promotionnel sans substance extractible
  - too_long    : > 50 000 chars (livres, résumés, coûteux à compiler)

Usage:
    uv run python scripts/audit_raw.py
    uv run python scripts/audit_raw.py --source medium
    uv run python scripts/audit_raw.py --output 03_OUTPUT/Reports/audit.json
    uv run python scripts/audit_raw.py --delete --category invalid --max-chars 1000
    uv run python scripts/audit_raw.py --delete --category invalid
"""

import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path

import frontmatter
from rich.console import Console
from rich.table import Table

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import get_settings

console = Console()
logger = logging.getLogger(__name__)

# Seuils
MIN_CHARS = 500
MAX_CHARS_THRESHOLD = 50_000

# Patterns articles invalides (CAPTCHA, erreurs d'extraction)
_INVALID_PATTERNS = [
    "performing security verification",
    "security service to protect against malicious bots",
    "please enable javascript",
    "just a moment",
    "403 forbidden",
    "404 not found",
    "access denied",
    "enable cookies",
    "verify you are human",
    "ddos protection",
    "checking your browser",
    "target url returned error",
]

# Patterns newsletters promotionnelles
_NEWSLETTER_PATTERNS = [
    "forward this email to",
    "forwarded this from a friend",
    "sign up to",
    "unsubscribe",
    "you're receiving this",
    "you are receiving this",
    "manage your subscription",
    "view in browser",
    "view this email in",
    "this email was sent to",
    "click here to unsubscribe",
    "update your preferences",
    "©",  # footer légal typique des newsletters
    "all rights reserved",
    "reached a new milestone",
    "we're thrilled to share",
    "we are thrilled to share",
    "thank you for being a subscriber",
    "thank you for subscribing",
    "our readership",
    "forwarded this newsletter",
]

# Nombre minimum de patterns newsletter pour déclencher la détection
_NEWSLETTER_MIN_MATCHES = 2


def _detect_category(content: str) -> tuple[str, str] | None:
    """Détecte si un article est polluant et retourne (catégorie, raison).

    Args:
        content: Contenu brut de l'article.

    Returns:
        Tuple (catégorie, raison) si polluant, None si article valide.
    """
    stripped = content.strip()
    lower = stripped.lower()

    # 1. Invalide : trop court
    if len(stripped) < MIN_CHARS:
        return ("invalid", f"Contenu trop court : {len(stripped)} chars")

    # 2. Invalide : CAPTCHA / erreur d'extraction
    for pattern in _INVALID_PATTERNS:
        if pattern in lower:
            return ("invalid", f"Page d'erreur détectée : '{pattern}'")

    # 3. Trop long : livres/résumés coûteux
    if len(stripped) > MAX_CHARS_THRESHOLD:
        return ("too_long", f"{len(stripped):,} chars (> {MAX_CHARS_THRESHOLD:,} seuil)")

    # 4. Newsletter promotionnelle
    matches = [p for p in _NEWSLETTER_PATTERNS if p in lower]
    if len(matches) >= _NEWSLETTER_MIN_MATCHES:
        matched_str = ", ".join(f"'{m}'" for m in matches[:3])
        return ("newsletter", f"{len(matches)} indicateurs : {matched_str}")

    return None


def _collect_articles(vault_path: Path, source: str) -> list[Path]:
    """Collecte les articles RAW selon la source.

    Args:
        vault_path: Chemin racine du vault.
        source: 'medium', 'substack' ou 'all'.

    Returns:
        Liste triée des chemins d'articles markdown.
    """
    raw_root = vault_path / "00_RAW" / "articles"
    sources_map = {
        "medium": [raw_root / "medium"],
        "substack": [raw_root / "substack" / "posts", raw_root / "substack" / "newsletters"],
        "all": [raw_root / "medium", raw_root / "substack"],
    }
    dirs = sources_map.get(source, [raw_root])
    articles: list[Path] = []
    for d in dirs:
        if d.exists():
            articles.extend(sorted(d.rglob("*.md")))
    return articles


def run_audit(vault_path: Path, source: str) -> list[dict]:
    """Scanne les articles RAW et retourne les fichiers polluants.

    Args:
        vault_path: Chemin racine du vault.
        source: Source à auditer.

    Returns:
        Liste de dicts décrivant chaque article polluant.
    """
    articles = _collect_articles(vault_path, source)
    console.print(f"[dim]Scan de {len(articles):,} articles ({source})...[/dim]")

    polluting: list[dict] = []

    for article_path in articles:
        try:
            post = frontmatter.load(str(article_path))
        except Exception as e:
            polluting.append(
                {
                    "file": str(article_path.relative_to(vault_path)),
                    "category": "invalid",
                    "reason": f"Frontmatter illisible : {e}",
                    "chars": 0,
                    "preview": "",
                    "wiki_compiled": False,
                }
            )
            continue

        content = post.content or ""
        result = _detect_category(content)

        if result is None:
            continue  # Article valide — on l'ignore

        category, reason = result
        preview = content.strip()[:200].replace("\n", " ")

        polluting.append(
            {
                "file": str(article_path.relative_to(vault_path)),
                "category": category,
                "reason": reason,
                "chars": len(content.strip()),
                "preview": preview,
                "wiki_compiled": bool(post.metadata.get("wiki_compiled", False)),
            }
        )

    return polluting


def save_report(polluting: list[dict], vault_path: Path, output_path: Path | None) -> Path:
    """Sauvegarde le rapport JSON dans 03_OUTPUT/Reports/.

    Args:
        polluting: Liste des articles polluants.
        vault_path: Chemin racine du vault.
        output_path: Chemin de sortie optionnel.

    Returns:
        Chemin du fichier rapport généré.
    """
    if output_path is None:
        reports_dir = vault_path / "03_OUTPUT" / "Reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        output_path = reports_dir / f"audit_raw_{date.today().isoformat()}.json"

    # Grouper par catégorie pour le rapport
    by_category: dict[str, list[dict]] = {}
    for item in polluting:
        cat = item["category"]
        by_category.setdefault(cat, []).append(item)

    report = {
        "generated": date.today().isoformat(),
        "total_polluting": len(polluting),
        "summary": {cat: len(items) for cat, items in by_category.items()},
        "articles": polluting,
    }

    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


def delete_articles(
    polluting: list[dict],
    vault_path: Path,
    category: str,
    max_chars: int | None,
) -> tuple[int, int]:
    """Supprime les articles polluants correspondant aux critères.

    Args:
        polluting: Liste des articles polluants issus de run_audit().
        vault_path: Chemin racine du vault.
        category: Catégorie à supprimer ('invalid', 'newsletter', 'too_long').
        max_chars: Si spécifié, supprime uniquement les articles <= max_chars.

    Returns:
        Tuple (supprimés, ignorés).
    """
    candidates = [a for a in polluting if a["category"] == category]
    if max_chars is not None:
        candidates = [a for a in candidates if a["chars"] <= max_chars]

    deleted = 0
    skipped = 0
    for item in candidates:
        file_path = vault_path / item["file"]
        if not file_path.exists():
            skipped += 1
            continue
        try:
            file_path.unlink()
            logger.info(f"Supprimé : {item['file']}")
            deleted += 1
        except OSError as e:
            logger.error(f"Impossible de supprimer {item['file']} : {e}")
            skipped += 1

    return deleted, skipped


def print_summary(polluting: list[dict]) -> None:
    """Affiche le résumé dans le terminal.

    Args:
        polluting: Liste des articles polluants.
    """
    # Compter par catégorie
    counts: dict[str, int] = {}
    for item in polluting:
        counts[item["category"]] = counts.get(item["category"], 0) + 1

    table = Table(title="Audit RAW — Articles polluants", show_header=True, header_style="bold red")
    table.add_column("Catégorie")
    table.add_column("Nombre", justify="right")
    table.add_column("Description")

    category_labels = {
        "invalid": ("invalid", "CAPTCHA, 403, page vide, frontmatter illisible"),
        "newsletter": ("newsletter", "Contenu promotionnel sans substance extractible"),
        "too_long": ("too_long", f"Plus de {MAX_CHARS_THRESHOLD:,} chars (livres, résumés)"),
    }

    for cat, (label, desc) in category_labels.items():
        count = counts.get(cat, 0)
        if count > 0:
            style = "red" if cat == "invalid" else "yellow" if cat == "newsletter" else "cyan"
            table.add_row(f"[{style}]{label}[/{style}]", str(count), desc)

    console.print(table)

    # Détail par catégorie
    for cat in ["invalid", "newsletter", "too_long"]:
        items = [i for i in polluting if i["category"] == cat]
        if not items:
            continue

        console.print(f"\n[bold]── {cat.upper()} ({len(items)} articles)[/bold]")
        for item in items[:10]:  # Max 10 par catégorie dans le terminal
            compiled_tag = " [dim][compilé][/dim]" if item["wiki_compiled"] else ""
            console.print(
                f"  [dim]{item['chars']:>7,} chars[/dim]  "
                f"{Path(item['file']).name[:60]}{compiled_tag}"
            )
            console.print(f"           [dim italic]{item['reason']}[/dim italic]")
        if len(items) > 10:
            console.print(f"  [dim]... et {len(items) - 10} autres (voir rapport JSON)[/dim]")


def parse_args() -> argparse.Namespace:
    """Parse les arguments CLI."""
    parser = argparse.ArgumentParser(
        description="Audit des articles RAW — détecte les fichiers polluants",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples:
  uv run python scripts/audit_raw.py
  uv run python scripts/audit_raw.py --source medium
  uv run python scripts/audit_raw.py --delete --category invalid --max-chars 1000
  uv run python scripts/audit_raw.py --delete --category invalid
        """,
    )
    parser.add_argument(
        "--source",
        choices=["medium", "substack", "all"],
        default="all",
        help="Source à auditer (défaut: all)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        metavar="PATH",
        help="Chemin du rapport JSON (défaut: 03_OUTPUT/Reports/audit_raw_YYYY-MM-DD.json)",
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Supprimer les articles correspondant aux critères (irréversible)",
    )
    parser.add_argument(
        "--category",
        choices=["invalid", "newsletter", "too_long"],
        default=None,
        metavar="CAT",
        help="Catégorie à supprimer avec --delete (invalid, newsletter, too_long)",
    )
    parser.add_argument(
        "--max-chars",
        type=int,
        default=None,
        metavar="N",
        help="Avec --delete : supprimer uniquement les articles <= N chars",
    )
    return parser.parse_args()


def main() -> int:
    """Point d'entrée du script d'audit."""
    args = parse_args()
    settings = get_settings()
    vault_path = Path(settings.get_vault_path())

    # Validation : --delete requiert --category
    if args.delete and not args.category:
        console.print(
            "[bold red]❌ --delete requiert --category (invalid, newsletter, too_long)[/bold red]"
        )
        return 1

    console.print("[bold]🔍 obsidian-wiki — Audit RAW[/bold]")
    console.print(f"[dim]Vault : {vault_path}[/dim]\n")

    polluting = run_audit(vault_path, args.source)

    if not polluting:
        console.print("[bold green]✅ Aucun article polluant détecté.[/bold green]")
        return 0

    print_summary(polluting)

    # Mode suppression
    if args.delete:
        candidates = [a for a in polluting if a["category"] == args.category]
        if args.max_chars is not None:
            candidates = [a for a in candidates if a["chars"] <= args.max_chars]

        if not candidates:
            console.print(f"\n[yellow]Aucun article à supprimer avec ces critères.[/yellow]")
            return 0

        # Résumé avant confirmation
        chars_filter = f" et <= {args.max_chars:,} chars" if args.max_chars else ""
        console.print(
            f"\n[bold red]⚠️  Suppression de {len(candidates)} articles "
            f"(catégorie={args.category}{chars_filter})[/bold red]"
        )
        console.print("[dim]Cette opération est IRRÉVERSIBLE.[/dim]")

        # Afficher les 5 premiers pour confirmation visuelle
        for item in candidates[:5]:
            console.print(f"  [dim]• {Path(item['file']).name}[/dim]")
        if len(candidates) > 5:
            console.print(f"  [dim]... et {len(candidates) - 5} autres[/dim]")

        confirm = console.input("\n[bold]Confirmer la suppression ? (oui/non) :[/bold] ")
        if confirm.strip().lower() not in ("oui", "o", "yes", "y"):
            console.print("[yellow]Annulé.[/yellow]")
            return 0

        deleted, skipped = delete_articles(polluting, vault_path, args.category, args.max_chars)
        console.print(
            f"\n[bold green]✅ {deleted} articles supprimés[/bold green]"
            + (f" [yellow]({skipped} ignorés)[/yellow]" if skipped else "")
        )
        return 0

    # Mode audit seul : sauvegarder le rapport
    report_path = save_report(polluting, vault_path, args.output)
    console.print(f"\n[bold green]✅ Rapport sauvegardé :[/bold green] {report_path}")
    console.print(
        "[dim]Révisez le rapport avant toute suppression. Aucun fichier n'a été modifié.[/dim]"
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
