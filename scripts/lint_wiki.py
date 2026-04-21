"""Script de health check du wiki.

Usage:
    uv run python scripts/lint_wiki.py
    uv run python scripts/lint_wiki.py --report
    uv run python scripts/lint_wiki.py --fix
    uv run python scripts/lint_wiki.py --enrich NAME
    uv run python scripts/lint_wiki.py --enrich-all --concurrency 5
"""

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

# Ajouter la racine du projet au PYTHONPATH
sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn
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
  uv run python scripts/lint_wiki.py --enrich-all --concurrency 5
  uv run python scripts/lint_wiki.py --enrich-all --concurrency 5 --limit 100
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
        "--merge-duplicates",
        action="store_true",
        help="Fusionner les doublons confirmés dans leur fiche canonique",
    )
    parser.add_argument(
        "--enrich",
        type=str,
        metavar="NAME",
        help="Enrichir un concept spécifique avec Gemini",
    )
    parser.add_argument(
        "--enrich-all",
        dest="enrich_all",
        action="store_true",
        help="Enrichir toutes les fiches avec définitions manquantes (mode async concurrent)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=5,
        metavar="N",
        help="Nombre de requêtes Gemini simultanées pour --enrich-all (défaut: 5)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Nombre maximum de fiches à enrichir avec --enrich-all",
    )
    parser.add_argument(
        "--provider",
        type=str,
        choices=["gemini", "inception"],
        default="gemini",
        help="Provider LLM à utiliser pour l'enrichissement (défaut: gemini)",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Nom du modèle à utiliser (défaut: gemini-2.5-flash-lite pour gemini, mercury2 pour inception)",
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
        # Vérifier la clé API selon le provider
        if args.provider == "inception":
            api_key = settings.get_inception_api_key()
            provider_name = "Inception Labs"
        else:
            api_key = settings.get_gemini_api_key()
            provider_name = "Gemini"

        if not api_key:
            console.print(f"[bold red]❌ Clé API {provider_name} non configurée[/bold red]")
            return 1

        console.print(f"[dim]Enrichissement de : {args.enrich!r} ({args.provider})...[/dim]")
        try:
            from src.lint.enricher import Enricher

            enricher = Enricher(provider=args.provider, model_name=args.model)
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

    # Mode enrichissement en masse (--enrich-all)
    if args.enrich_all:
        # Vérifier la clé API selon le provider
        if args.provider == "inception":
            api_key = settings.get_inception_api_key()
            provider_name = "Inception Labs"
            model_display = args.model or "mercury-2"
        else:
            api_key = settings.get_gemini_api_key()
            provider_name = "Gemini"
            model_display = args.model or settings.gemini_model_wiki

        if not api_key:
            console.print(f"[bold red]❌ Clé API {provider_name} non configurée[/bold red]")
            return 1

        console.print(
            f"[dim]Mode : Enrich-all async ({args.provider}/{model_display}, concurrence={args.concurrency})[/dim]\n"
        )

        # Détecter les fiches avec définitions manquantes
        console.print("[dim]Détection des fiches à enrichir...[/dim]")
        try:
            checker = HealthChecker()
            missing = checker.check_missing_definitions()
        except Exception as e:
            console.print(f"[bold red]❌ Erreur détection : {e}[/bold red]")
            return 1

        if not missing:
            console.print("[green]✅ Aucune définition manquante — wiki complet ![/green]")
            return 0

        # Appliquer --limit si demandé
        if args.limit:
            missing = missing[: args.limit]

        console.print(
            f"[bold]{len(missing)} fiches à enrichir[/bold] "
            f"[dim](concurrence={args.concurrency})[/dim]\n"
        )

        try:
            from src.lint.enricher import Enricher

            enricher = Enricher(provider=args.provider, model_name=args.model)

            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                console=console,
                transient=True,
            ) as progress:
                progress.add_task(
                    f"Enrichissement async ({args.provider}, concurrence={args.concurrency}) en cours...",
                    total=None,
                )
                batch = enricher.enrich_all_async(missing, concurrency=args.concurrency)

        except Exception as e:
            console.print(f"[bold red]❌ Erreur enrichissement : {e}[/bold red]")
            return 1

        # Afficher le résumé
        table = Table(
            title="Résultat enrichissement",
            show_header=True,
            header_style="bold blue",
        )
        table.add_column("Métrique", style="bold")
        table.add_column("Valeur", justify="right")
        table.add_row("Fiches enrichies", str(batch.total_enriched), style="green")
        table.add_row("Erreurs", str(batch.total_errors), style="red" if batch.total_errors else "")
        table.add_row("Tokens input", f"{batch.total_input_tokens:,}")
        table.add_row("Tokens output", f"{batch.total_output_tokens:,}")
        table.add_row("Coût total", f"${batch.total_cost:.4f}")
        if batch.total_enriched > 0:
            table.add_row("Coût/fiche", f"${batch.total_cost / batch.total_enriched:.5f}")
        console.print(table)

        if batch.total_errors > 0:
            console.print(f"\n[bold yellow]Erreurs détectées ({batch.total_errors}) :[/bold yellow]")
            errors = [r for r in batch.results if not r.success]
            for r in errors[:10]:
                console.print(f"  • [dim]{r.concept_name}[/dim] : {r.error}")
            if len(errors) > 10:
                console.print(f"  [dim]... et {len(errors) - 10} autres[/dim]")
            console.print(f"\n[bold yellow]⚠️  Terminé avec {batch.total_errors} erreur(s).[/bold yellow]")
        else:
            console.print(f"\n[bold green]✅ {batch.total_enriched} fiches enrichies avec succès.[/bold green]")

        return 0

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

    # Fusionner les doublons si --merge-duplicates
    if args.merge_duplicates:  # argparse convertit --merge-duplicates → merge_duplicates
        if not report.duplicate_groups:
            console.print("\n[green]Aucun doublon à fusionner.[/green]")
        else:
            console.print(
                f"\n[bold]Fusion de {len(report.duplicate_groups)} groupe(s) de doublons...[/bold]"
            )
            for group in report.duplicate_groups:
                dups = ", ".join(p.stem for p in group.duplicates)
                console.print(
                    f"  [cyan]{group.canonical.stem}[/cyan] ← absorbe → [dim]{dups}[/dim]"
                )
            deleted = checker.merge_duplicates(report.duplicate_groups)
            console.print(
                f"[green]✅ {deleted} doublon(s) supprimé(s), wikilinks redirigés.[/green]"
            )

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
