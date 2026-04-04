"""Script de compilation wiki — transforme les articles RAW en fiches concepts.

Usage:
    uv run python scripts/compile_wiki.py
    uv run python scripts/compile_wiki.py --source medium
    uv run python scripts/compile_wiki.py --source substack --limit 5
    uv run python scripts/compile_wiki.py --force
    uv run python scripts/compile_wiki.py --stats
"""

import argparse
import logging
import sys
from pathlib import Path

# Ajouter la racine du projet au PYTHONPATH
sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

from src.config import get_settings
from src.wiki.compiler import WikiCompiler
from src.wiki.models import BatchCompilationResult

console = Console()


def setup_logging(level: str = "INFO") -> None:
    """Configure le logging.

    Args:
        level: Niveau de log.
    """
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        level=getattr(logging, level),
    )


def cmd_stats(compiler: WikiCompiler) -> None:
    """Affiche les statistiques du vault sans compiler.

    Args:
        compiler: Instance WikiCompiler initialisée.
    """
    stats = compiler.get_compilation_stats()

    table = Table(title="Statistiques du vault", show_header=True, header_style="bold blue")
    table.add_column("Métrique")
    table.add_column("Valeur", justify="right")

    table.add_row("Articles RAW", str(stats["total_raw"]))
    table.add_row("Articles compilés", str(stats["total_compiled"]), style="green")
    table.add_row("En attente", str(stats["pending_count"]), style="yellow")
    table.add_row("Fiches wiki", str(stats["total_wiki_fiches"]), style="cyan")

    console.print(table)


def print_batch_result(result: BatchCompilationResult) -> None:
    """Affiche le résumé d'un batch de compilation.

    Args:
        result: Résultat du batch.
    """
    # Tableau récapitulatif
    table = Table(title="Résultat de compilation", show_header=True, header_style="bold")
    table.add_column("Métrique")
    table.add_column("Valeur", justify="right")

    table.add_row("Articles compilés", str(result.total_compiled), style="green")
    table.add_row("Articles ignorés", str(result.total_skipped), style="dim")
    table.add_row("Fiches créées", str(result.total_concepts_created), style="cyan")
    table.add_row("Fiches mises à jour", str(result.total_concepts_updated), style="blue")
    if result.total_errors > 0:
        table.add_row("Erreurs", str(result.total_errors), style="red")

    console.print(table)

    # Détail des erreurs
    if result.total_errors > 0:
        console.print("\n[bold red]Erreurs détectées :[/bold red]")
        for r in result.results:
            for err in r.errors:
                console.print(f"  • [dim]{r.article_path.name}[/dim] : {err}")


def parse_args() -> argparse.Namespace:
    """Parse les arguments CLI.

    Returns:
        Namespace parsé.
    """
    parser = argparse.ArgumentParser(
        description="Compile les articles RAW en fiches wiki via Gemini",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples:
  uv run python scripts/compile_wiki.py --stats
  uv run python scripts/compile_wiki.py --source medium --limit 10
  uv run python scripts/compile_wiki.py --source all --force
        """,
    )
    parser.add_argument(
        "--source",
        choices=["medium", "substack", "all"],
        default="all",
        help="Source à compiler (défaut: all)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Nombre maximum d'articles à compiler",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Recompiler les articles déjà compilés",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Afficher les statistiques sans compiler",
    )
    parser.add_argument(
        "--no-index",
        action="store_true",
        help="Ne pas régénérer l'index maître après compilation",
    )
    return parser.parse_args()


def main() -> int:
    """Point d'entrée du script de compilation.

    Returns:
        Code de retour (0 = succès, 1 = erreur).
    """
    args = parse_args()
    settings = get_settings()
    setup_logging(settings.log_level)

    # Vérification de la clé API
    if not settings.gemini_api_key and not args.stats:
        console.print("[bold red]❌ GEMINI_API_KEY non configurée dans .env[/bold red]")
        console.print("Copiez .env.example vers .env et renseignez votre clé API.")
        return 1

    console.print("[bold]🧠 obsidian-wiki — Compilation[/bold]")
    console.print(f"[dim]Vault : {settings.vault_path}[/dim]")

    compiler = WikiCompiler()

    # Mode stats uniquement
    if args.stats:
        cmd_stats(compiler)
        return 0

    console.print(
        f"[dim]Source : {args.source} | "
        f"Limit : {args.limit or 'aucune'} | "
        f"Force : {args.force}[/dim]\n"
    )

    # Compilation
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task("Compilation en cours...", total=None)

        result = compiler.batch_compile(
            source=args.source,
            limit=args.limit,
            force=args.force,
            rebuild_index=not args.no_index,
        )
        progress.update(task, completed=True)

    print_batch_result(result)

    if result.total_errors == 0:
        console.print("\n[bold green]✅ Compilation terminée.[/bold green]")
        return 0
    else:
        console.print(
            f"\n[bold yellow]⚠️  Terminé avec {result.total_errors} erreur(s).[/bold yellow]"
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
