"""Script de compilation wiki — transforme les articles RAW en fiches concepts.

Usage:
    uv run python scripts/compile_wiki.py
    uv run python scripts/compile_wiki.py --source medium
    uv run python scripts/compile_wiki.py --source substack --limit 5
    uv run python scripts/compile_wiki.py --force
    uv run python scripts/compile_wiki.py --stats
    uv run python scripts/compile_wiki.py --batch
    uv run python scripts/compile_wiki.py --batch --source medium --limit 100
    uv run python scripts/compile_wiki.py --batch-poll JOB_NAME
"""

import argparse
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Ajouter la racine du projet au PYTHONPATH
sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn
from rich.table import Table

from src.config import get_settings
from src.wiki.compiler import WikiCompiler
from src.wiki.models import BatchCompilationResult

console = Console()

# Prix par million de tokens (input, output) pour chaque modèle connu
# Standard pricing
_MODEL_PRICING: dict[str, tuple[float, float]] = {
    "gemini-2.5-flash-lite": (0.10, 0.40),
    "gemini-2.5-flash": (0.15, 0.60),
    "gemini-2.5-pro": (1.25, 5.00),
    "gemini-2.0-flash-lite": (0.075, 0.30),
    "gemini-2.0-flash": (0.10, 0.40),
}

# Batch API pricing (50% discount)
_MODEL_BATCH_PRICING: dict[str, tuple[float, float]] = {
    "gemini-2.5-flash-lite": (0.05, 0.20),
    "gemini-2.5-flash": (0.075, 0.30),
    "gemini-2.5-pro": (0.625, 2.50),
    "gemini-2.0-flash-lite": (0.0375, 0.15),
    "gemini-2.0-flash": (0.05, 0.20),
}


LOG_FILE = Path(__file__).parent.parent / "logs" / "compile_wiki.log"
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(level: str = "INFO") -> None:
    """Configure le logging console + fichier rotatif.

    Fichier : logs/compile_wiki.log (5 MB × 3 fichiers max).

    Args:
        level: Niveau de log (DEBUG, INFO, WARNING, ERROR).
    """
    log_level = getattr(logging, level)
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    formatter = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT)

    # Handler console
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # Réduire le bruit des librairies HTTP tierces (même en DEBUG)
    for noisy_logger in ("httpcore", "httpx", "urllib3", "google_genai.models"):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)

    # Handler fichier rotatif
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=5 * 1024 * 1024,  # 5 MB
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)


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


def _get_model_pricing(model_name: str, batch: bool = False) -> tuple[float, float] | None:
    """Retourne le prix (input, output) par million de tokens pour un modèle.

    Args:
        model_name: Identifiant du modèle Gemini.
        batch: Si True, retourne les prix Batch API (50% réduction).

    Returns:
        Tuple (prix_input, prix_output) ou None si modèle inconnu.
    """
    pricing_table = _MODEL_BATCH_PRICING if batch else _MODEL_PRICING
    for key, prices in pricing_table.items():
        if key in model_name:
            return prices
    return None


def print_batch_result(
    result: BatchCompilationResult, model_name: str, batch: bool = False
) -> None:
    """Affiche le résumé d'un batch de compilation.

    Args:
        result: Résultat du batch.
        model_name: Nom du modèle utilisé (pour le calcul de coût réel).
        batch: Si True, utilise les prix Batch API.
    """
    mode_label = "Batch API" if batch else "Standard"
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

    # Tableau coût réel (si tokens capturés via usage_metadata)
    total_tokens = result.total_input_tokens + result.total_output_tokens
    if total_tokens > 0:
        pricing = _get_model_pricing(model_name, batch=batch)
        cost_table = Table(
            title=f"Coût API réel ({mode_label})",
            show_header=True,
            header_style="bold magenta",
        )
        cost_table.add_column("Métrique")
        cost_table.add_column("Valeur", justify="right")

        cost_table.add_row("Tokens input", f"{result.total_input_tokens:,}")
        cost_table.add_row("Tokens output", f"{result.total_output_tokens:,}")
        cost_table.add_row("Tokens total", f"{total_tokens:,}")

        if pricing:
            input_price, output_price = pricing
            input_cost = (result.total_input_tokens / 1_000_000) * input_price
            output_cost = (result.total_output_tokens / 1_000_000) * output_price
            total_cost = input_cost + output_cost
            cost_table.add_row("Coût input", f"${input_cost:.5f}", style="yellow")
            cost_table.add_row("Coût output", f"${output_cost:.5f}", style="yellow")
            cost_table.add_row("Coût total", f"${total_cost:.4f}", style="bold green")

            if result.total_compiled > 0:
                cost_per_article = total_cost / result.total_compiled
                proj_5740 = cost_per_article * 5740
                cost_table.add_row("Coût/article", f"${cost_per_article:.5f}", style="dim")
                cost_table.add_row(
                    "Projection 5 740 articles", f"${proj_5740:.2f}", style="bold cyan"
                )
        else:
            cost_table.add_row("Coût", "[dim]modèle inconnu dans la grille tarifaire[/dim]")

        console.print(cost_table)

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
  uv run python scripts/compile_wiki.py --batch
  uv run python scripts/compile_wiki.py --batch --source medium --limit 100
  uv run python scripts/compile_wiki.py --batch-poll jobs/123456
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
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        metavar="MODEL",
        help=(
            "Override du modèle Gemini (ex: gemini-2.5-flash-lite). "
            "Prend la priorité sur GEMINI_MODEL_WIKI dans .env"
        ),
    )
    parser.add_argument(
        "--batch",
        action="store_true",
        help="Utiliser la Gemini Batch API (50%% moins cher, asynchrone)",
    )
    parser.add_argument(
        "--batch-poll",
        type=str,
        default=None,
        metavar="JOB_NAME",
        help="Récupérer les résultats d'un batch job existant",
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

    # Vérification de la clé API (depuis .env ou variables d'environnement système)
    gemini_key = settings.get_gemini_api_key()
    if not gemini_key and not args.stats:
        console.print("[bold red]❌ Clé API non configurée[/bold red]")
        console.print("Ajoutez GEMINI_API_KEY ou GOOGLE_API_KEY dans .env ou ~/.zshenv")
        return 1

    is_batch = args.batch or args.batch_poll
    mode_label = "Batch API" if is_batch else "Standard"

    console.print("[bold]🧠 obsidian-wiki — Compilation[/bold]")
    console.print(f"[dim]Vault : {settings.get_vault_path()}[/dim]")

    effective_model = args.model or settings.gemini_model_wiki
    model_label = (
        f"{effective_model} [bold yellow](override)[/bold yellow]"
        if args.model
        else effective_model
    )
    console.print(f"[dim]Modèle : {model_label} | Mode : {mode_label}[/dim]")

    compiler = WikiCompiler(model_override=args.model)

    # Mode stats uniquement
    if args.stats:
        cmd_stats(compiler)
        return 0

    # Mode poll d'un batch job existant
    if args.batch_poll:
        console.print(f"[dim]Poll batch job : {args.batch_poll}[/dim]\n")
        try:
            result = compiler.poll_batch_job(
                job_name=args.batch_poll,
                rebuild_index=not args.no_index,
            )
        except RuntimeError as e:
            console.print(f"[bold red]❌ Erreur : {e}[/bold red]")
            return 1

        print_batch_result(result, effective_model, batch=True)
        if result.total_errors == 0:
            console.print("\n[bold green]✅ Compilation batch terminée.[/bold green]")
            return 0
        else:
            console.print(
                f"\n[bold yellow]⚠️  Terminé avec {result.total_errors} erreur(s).[/bold yellow]"
            )
            return 1

    console.print(
        f"[dim]Source : {args.source} | "
        f"Limit : {args.limit or 'aucune'} | "
        f"Force : {args.force} | "
        f"Mode : {mode_label}[/dim]\n"
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
        task = progress.add_task(
            f"Compilation {mode_label} en cours...",
            total=None,
        )

        if args.batch:
            try:
                result = compiler.batch_compile_api(
                    source=args.source,
                    limit=args.limit,
                    force=args.force,
                    rebuild_index=not args.no_index,
                )
            except RuntimeError as e:
                console.print(f"[bold red]❌ Erreur batch : {e}[/bold red]")
                return 1
        else:
            result = compiler.batch_compile(
                source=args.source,
                limit=args.limit,
                force=args.force,
                rebuild_index=not args.no_index,
            )
        progress.update(task, completed=True)

    print_batch_result(result, effective_model, batch=is_batch)

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
