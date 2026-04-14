"""Script de health check du wiki.

Usage:
    uv run python scripts/lint_wiki.py
    uv run python scripts/lint_wiki.py --report
    uv run python scripts/lint_wiki.py --fix
    uv run python scripts/lint_wiki.py --enrich NAME
"""

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

# Ajouter la racine du projet au PYTHONPATH
sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console
from rich.table import Table
from rich.text import Text

from src.config import get_settings
from src.lint.health_checker import HealthChecker
from src.lint.models import HealthReport

console = Console()

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(level: str = "INFO") -> None:
    """Configure le logging.

    Args:
        level: Niveau de log (DEBUG, INFO, WARNING, ERROR).
    """
    log_level = getattr(logging, level)
    logging.basicConfig(format=LOG_FORMAT, datefmt=LOG_DATE_FORMAT, level=log_level)
    for noisy_logger in ("httpcore", "httpx", "urllib3", "google_genai.models"):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)


def parse_args() -> argparse.Namespace:
    """Parse les arguments CLI.

    Returns:
        Namespace parsé.
    """
    parser = argparse.ArgumentParser(
        description="Analyse la santé et cohérence du wiki Obsidian",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples:
  uv run python scripts/lint_wiki.py
  uv run python scripts/lint_wiki.py --report
  uv run python scripts/lint_wiki.py --fix
  uv run python scripts/lint_wiki.py --enrich GraphRAG
        """,
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="Sauvegarder le rapport dans 03_OUTPUT/Reports/",
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Appliquer corrections automatiques (suppression des liens cassés)",
    )
    parser.add_argument(
        "--enrich",
        type=str,
        metavar="NAME",
        help="Enrichir un concept spécifique avec Gemini",
    )
    return parser.parse_args()


def _score_color(score: int) -> str:
    """Retourne la couleur Rich selon le score.

    Args:
        score: Score de santé 0-100.

    Returns:
        Nom de couleur Rich.
    """
    if score >= 80:
        return "green"
    if score >= 50:
        return "yellow"
    return "red"


def print_health_report(report: HealthReport) -> None:
    """Affiche le rapport de santé avec Rich.

    Args:
        report: Rapport de santé à afficher.
    """
    # Score de santé
    color = _score_color(report.score)
    score_text = Text(f"Score de santé : {report.score}/100", style=f"bold {color}")
    console.print(score_text)
    console.print(f"[dim]Fiches wiki totales : {report.total_wiki_fiches}[/dim]\n")

    # Tableau des problèmes
    table = Table(
        title="Rapport de santé du wiki",
        show_header=True,
        header_style="bold blue",
    )
    table.add_column("Catégorie", style="bold")
    table.add_column("Nombre", justify="right")
    table.add_column("Statut")

    def status_icon(count: int, warn_threshold: int = 1) -> str:
        if count == 0:
            return "[green]✅ OK[/green]"
        if count < warn_threshold * 3:
            return "[yellow]⚠️  Attention[/yellow]"
        return "[red]❌ Critique[/red]"

    table.add_row(
        "Liens cassés",
        str(len(report.broken_links)),
        status_icon(len(report.broken_links)),
    )
    table.add_row(
        "Concepts orphelins",
        str(len(report.orphaned_concepts)),
        status_icon(len(report.orphaned_concepts), warn_threshold=3),
    )
    table.add_row(
        "Groupes de doublons",
        str(len(report.duplicate_groups)),
        status_icon(len(report.duplicate_groups)),
    )
    table.add_row(
        "Définitions manquantes",
        str(len(report.missing_definitions)),
        status_icon(len(report.missing_definitions), warn_threshold=3),
    )

    console.print(table)

    # Détail des liens cassés
    if report.broken_links:
        console.print("\n[bold red]Liens cassés :[/bold red]")
        for link in report.broken_links[:20]:  # Limiter l'affichage
            console.print(
                f"  • [dim]{link.source_file.name}:{link.line_number}[/dim] → "
                f"[[{link.link_target}]]"
            )
        if len(report.broken_links) > 20:
            console.print(f"  [dim]... et {len(report.broken_links) - 20} autres[/dim]")

    # Détail des doublons
    if report.duplicate_groups:
        console.print("\n[bold yellow]Doublons potentiels :[/bold yellow]")
        for group in report.duplicate_groups[:10]:
            dups = ", ".join(p.stem for p in group.duplicates)
            console.print(f"  • [cyan]{group.canonical.stem}[/cyan] ↔ {dups}")

    # Détail des définitions manquantes
    if report.missing_definitions:
        console.print("\n[bold yellow]Définitions manquantes :[/bold yellow]")
        for item in report.missing_definitions[:15]:
            console.print(f"  • [dim]{item.title}[/dim] (section : {item.section})")
        if len(report.missing_definitions) > 15:
            console.print(f"  [dim]... et {len(report.missing_definitions) - 15} autres[/dim]")


def save_report(report: HealthReport, vault_path: Path) -> Path:
    """Sauvegarde le rapport de santé en Markdown.

    Args:
        report: Rapport de santé à sauvegarder.
        vault_path: Chemin racine du vault.

    Returns:
        Chemin du fichier généré.
    """
    output_dir = vault_path / "03_OUTPUT" / "Reports"
    output_dir.mkdir(parents=True, exist_ok=True)

    today = date.today().isoformat()
    output_path = output_dir / f"{today}_health-check.md"

    color_label = (
        "sain" if report.score >= 80 else ("dégradé" if report.score >= 50 else "critique")
    )

    lines = [
        "---",
        f'title: "Health Check Wiki — {today}"',
        f"date: {today}",
        f"score: {report.score}",
        "---",
        "",
        f"# Health Check Wiki — {today}",
        "",
        f"> Score : **{report.score}/100** ({color_label})",
        f"> Fiches wiki : {report.total_wiki_fiches}",
        "",
        "## Résumé",
        "",
        "| Catégorie | Nombre |",
        "|-----------|--------|",
        f"| Liens cassés | {len(report.broken_links)} |",
        f"| Concepts orphelins | {len(report.orphaned_concepts)} |",
        f"| Groupes de doublons | {len(report.duplicate_groups)} |",
        f"| Définitions manquantes | {len(report.missing_definitions)} |",
        "",
    ]

    if report.broken_links:
        lines += ["## Liens cassés", ""]
        for link in report.broken_links:
            lines.append(f"- `{link.source_file.name}:{link.line_number}` → [[{link.link_target}]]")
        lines.append("")

    if report.duplicate_groups:
        lines += ["## Doublons potentiels", ""]
        for group in report.duplicate_groups:
            dups = ", ".join(f"[[{p.stem}]]" for p in group.duplicates)
            lines.append(f"- [[{group.canonical.stem}]] ↔ {dups}")
        lines.append("")

    if report.missing_definitions:
        lines += ["## Définitions manquantes", ""]
        for item in report.missing_definitions:
            lines.append(f"- [[{item.title}]] — section : {item.section}")
        lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")
    return output_path


def apply_fixes(report: HealthReport) -> int:
    """Applique les corrections automatiques.

    Supprime les liens cassés des fichiers sources.

    Args:
        report: Rapport de santé avec les problèmes détectés.

    Returns:
        Nombre de corrections appliquées.
    """
    import re

    fixes_applied = 0

    if not report.broken_links:
        console.print("[green]Aucun lien cassé à corriger.[/green]")
        return 0

    # Grouper les liens cassés par fichier source
    files_to_fix: dict[Path, list[str]] = {}
    for link in report.broken_links:
        if link.source_file not in files_to_fix:
            files_to_fix[link.source_file] = []
        files_to_fix[link.source_file].append(link.link_target)

    for source_file, targets in files_to_fix.items():
        try:
            content = source_file.read_text(encoding="utf-8")
            original = content
            for target in targets:
                # Supprimer le wikilink (remplacer [[target]] par le texte seul)
                pattern = re.compile(rf"\[\[{re.escape(target)}(?:\|([^\]]*))?\]\]")
                # Capturer target dans la closure via argument par défaut
                content = pattern.sub(lambda m, t=target: m.group(1) or t, content)

            if content != original:
                source_file.write_text(content, encoding="utf-8")
                fixes_applied += len(targets)
                console.print(
                    f"[green]✅ Corrigé : {source_file.name} ({len(targets)} lien(s))[/green]"
                )
        except OSError as e:
            console.print(f"[red]❌ Erreur correction {source_file.name} : {e}[/red]")

    return fixes_applied


def main() -> int:
    """Point d'entrée du script de health check.

    Returns:
        Code de retour (0 = succès, 1 = erreur).
    """
    args = parse_args()
    settings = get_settings()
    setup_logging(settings.log_level)

    console.print("[bold]🧠 obsidian-wiki — Health Check[/bold]")
    console.print(f"[dim]Vault : {settings.get_vault_path()}[/dim]\n")

    # Mode enrichissement d'un concept spécifique
    if args.enrich:
        gemini_key = settings.get_gemini_api_key()
        if not gemini_key:
            console.print("[bold red]❌ Clé API non configurée[/bold red]")
            return 1

        console.print(f"[dim]Enrichissement de : {args.enrich!r}...[/dim]")
        try:
            from src.lint.enricher import Enricher

            enricher = Enricher()
            success = enricher.enrich_concept(args.enrich)
            if success:
                console.print(f"[green]✅ Concept enrichi : {args.enrich}[/green]")
                return 0
            else:
                console.print(f"[red]❌ Échec enrichissement : {args.enrich}[/red]")
                return 1
        except Exception as e:
            console.print(f"[bold red]❌ Erreur : {e}[/bold red]")
            return 1

    # Health check complet
    console.print("[dim]Analyse en cours...[/dim]\n")
    try:
        checker = HealthChecker()
        report = checker.run_full_check()
    except Exception as e:
        console.print(f"[bold red]❌ Erreur lors du health check : {e}[/bold red]")
        return 1

    # Afficher le rapport
    print_health_report(report)

    # Sauvegarder si --report
    if args.report:
        vault_path = Path(settings.get_vault_path())
        try:
            report_path = save_report(report, vault_path)
            console.print(f"\n[green]✅ Rapport sauvegardé : {report_path}[/green]")
        except Exception as e:
            console.print(f"\n[yellow]⚠️  Erreur sauvegarde rapport : {e}[/yellow]")

    # Appliquer les corrections si --fix
    if args.fix:
        console.print("\n[bold]Application des corrections...[/bold]")
        fixes = apply_fixes(report)
        console.print(f"[green]{fixes} correction(s) appliquée(s).[/green]")

    # Code de retour selon la santé
    if report.is_healthy:
        console.print(f"\n[bold green]✅ Wiki sain (score: {report.score}/100)[/bold green]")
        return 0
    else:
        color = "yellow" if report.score >= 50 else "red"
        console.print(f"\n[bold {color}]⚠️  Wiki dégradé (score: {report.score}/100)[/bold {color}]")
        return 0  # Ne pas retourner 1 pour ne pas bloquer les pipelines


if __name__ == "__main__":
    sys.exit(main())
