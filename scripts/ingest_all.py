"""Script d'ingestion unifié — lance tous les bridges vers le vault.

Usage:
    uv run python scripts/ingest_all.py
    uv run python scripts/ingest_all.py --source medium
    uv run python scripts/ingest_all.py --source substack
    uv run python scripts/ingest_all.py --force
"""

import argparse
import logging
import sys
from pathlib import Path

# Ajouter la racine du projet au PYTHONPATH
sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console
from rich.table import Table

from bridges.medium_bridge import MediumBridge, SyncResult
from bridges.substack_bridge import SubstackBridge, SubstackSyncResult
from src.config import get_settings

console = Console()


def setup_logging(level: str = "INFO") -> None:
    """Configure le logging avec le niveau spécifié.

    Args:
        level: Niveau de log (DEBUG, INFO, WARNING, ERROR).
    """
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        level=getattr(logging, level),
    )


def run_medium_bridge(*, force: bool = False) -> SyncResult | None:
    """Exécute le bridge Medium.

    Args:
        force: Si True, écrase les fichiers existants.

    Returns:
        SyncResult ou None si le répertoire source est absent.
    """
    console.print("\n[bold blue]📰 Bridge Medium[/bold blue]")
    try:
        bridge = MediumBridge()
        result = bridge.sync_to_raw(force=force)
        _print_medium_result(result)
        return result
    except FileNotFoundError as e:
        console.print(f"[yellow]⚠️  {e}[/yellow]")
        console.print("[dim]Source Medium ignorée.[/dim]")
        return None


def run_substack_bridge(*, force: bool = False) -> SubstackSyncResult | None:
    """Exécute le bridge Substack.

    Args:
        force: Si True, écrase les fichiers existants.

    Returns:
        SubstackSyncResult ou None si le répertoire source est absent.
    """
    console.print("\n[bold magenta]📧 Bridge Substack[/bold magenta]")
    try:
        bridge = SubstackBridge()
        result = bridge.sync_all(force=force)
        _print_substack_result(result)
        return result
    except FileNotFoundError as e:
        console.print(f"[yellow]⚠️  {e}[/yellow]")
        console.print("[dim]Source Substack ignorée.[/dim]")
        return None


def _print_medium_result(result: SyncResult) -> None:
    """Affiche le résultat du bridge Medium dans un tableau Rich.

    Args:
        result: Résultat de synchronisation Medium.
    """
    table = Table(show_header=True, header_style="bold")
    table.add_column("Statut")
    table.add_column("Nombre", justify="right")
    table.add_row("✅ Copiés", str(len(result.synced)), style="green")
    table.add_row("⏭️  Ignorés", str(len(result.skipped)), style="dim")
    if result.errors:
        table.add_row("❌ Erreurs", str(len(result.errors)), style="red")
    console.print(table)

    if result.errors:
        console.print("[red]Erreurs :[/red]")
        for path, err in result.errors:
            console.print(f"  • {path.name}: {err}")


def _print_substack_result(result: SubstackSyncResult) -> None:
    """Affiche le résultat du bridge Substack dans un tableau Rich.

    Args:
        result: Résultat de synchronisation Substack.
    """
    table = Table(show_header=True, header_style="bold")
    table.add_column("Type")
    table.add_column("Copiés", justify="right")
    table.add_column("Ignorés", justify="right")
    table.add_row(
        "Posts",
        str(len(result.posts_synced)),
        str(len(result.posts_skipped)),
    )
    table.add_row(
        "Newsletters",
        str(len(result.newsletters_synced)),
        str(len(result.newsletters_skipped)),
    )
    console.print(table)

    if result.errors:
        console.print("[red]Erreurs :[/red]")
        for path, err in result.errors:
            console.print(f"  • {path.name}: {err}")


def parse_args() -> argparse.Namespace:
    """Parse les arguments CLI.

    Returns:
        Namespace avec les arguments parsés.
    """
    parser = argparse.ArgumentParser(
        description="Ingestion unifiée des sources vers le vault Obsidian",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples:
  uv run python scripts/ingest_all.py
  uv run python scripts/ingest_all.py --source medium
  uv run python scripts/ingest_all.py --source substack --force
        """,
    )
    parser.add_argument(
        "--source",
        choices=["medium", "substack", "all"],
        default="all",
        help="Source à synchroniser (défaut: all)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Écraser les fichiers existants",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simuler sans copier (à implémenter)",
    )
    return parser.parse_args()


def main() -> int:
    """Point d'entrée principal du script d'ingestion.

    Returns:
        Code de retour (0 = succès, 1 = erreur).
    """
    args = parse_args()
    settings = get_settings()
    setup_logging(settings.log_level)

    console.print("[bold]🔄 obsidian-wiki — Ingestion[/bold]")
    console.print(f"[dim]Vault : {settings.vault_path}[/dim]")
    console.print(f"[dim]Source : {args.source} | Force : {args.force}[/dim]")

    if args.dry_run:
        console.print("[yellow]Mode dry-run activé (pas encore implémenté)[/yellow]")
        return 0

    errors_count = 0

    if args.source in ("medium", "all"):
        result = run_medium_bridge(force=args.force)
        if result and result.errors:
            errors_count += len(result.errors)

    if args.source in ("substack", "all"):
        result_sub = run_substack_bridge(force=args.force)
        if result_sub and result_sub.errors:
            errors_count += len(result_sub.errors)

    console.print()
    if errors_count == 0:
        console.print("[bold green]✅ Ingestion terminée sans erreur.[/bold green]")
        return 0
    else:
        console.print(f"[bold red]⚠️  Ingestion terminée avec {errors_count} erreur(s).[/bold red]")
        return 1


if __name__ == "__main__":
    sys.exit(main())
