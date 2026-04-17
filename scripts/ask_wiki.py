"""Script Q&A — interroge le wiki en langage naturel.

Usage:
    uv run python scripts/ask_wiki.py "Qu'est-ce que GraphRAG ?"
    uv run python scripts/ask_wiki.py "Explique le RAG" --verbose
    uv run python scripts/ask_wiki.py "Topic X" --save
"""

import argparse
import logging
import sys
from pathlib import Path

# Ajouter la racine du projet au PYTHONPATH
sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from src.config import get_settings
from src.qa.engine import QAEngine

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
    # Réduire le bruit des librairies HTTP tierces
    for noisy_logger in ("httpcore", "httpx", "urllib3", "google_genai.models"):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)


def parse_args() -> argparse.Namespace:
    """Parse les arguments CLI.

    Returns:
        Namespace parsé.
    """
    parser = argparse.ArgumentParser(
        description="Interroge le wiki Obsidian en langage naturel",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples:
  uv run python scripts/ask_wiki.py "Qu'est-ce que GraphRAG ?"
  uv run python scripts/ask_wiki.py "Explique le RAG" --verbose
  uv run python scripts/ask_wiki.py "Topic X" --save
        """,
    )
    parser.add_argument(
        "question",
        help="Question à poser au wiki",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Afficher les statistiques de tokens",
    )
    parser.add_argument(
        "--save",
        action="store_true",
        help="Sauvegarder la réponse dans 03_OUTPUT/Reports/",
    )
    parser.add_argument(
        "--max-sources",
        type=int,
        default=10,
        metavar="N",
        help="Nombre maximum de fiches wiki à utiliser (défaut: 10)",
    )
    return parser.parse_args()


def main() -> int:
    """Point d'entrée du script Q&A.

    Returns:
        Code de retour (0 = succès, 1 = erreur).
    """
    args = parse_args()
    settings = get_settings()
    setup_logging(settings.log_level)

    # Vérification de la clé API
    gemini_key = settings.get_gemini_api_key()
    if not gemini_key:
        console.print("[bold red]❌ Clé API non configurée[/bold red]")
        console.print("Ajoutez GEMINI_API_KEY ou GEMINI_API_KEY_2 dans .env ou ~/.zshenv")
        return 1

    console.print("[bold]🧠 obsidian-wiki — Q&A[/bold]")
    console.print(f"[dim]Vault : {settings.get_vault_path()}[/dim]\n")

    # Afficher la question
    console.print(Panel(Text(args.question, style="bold"), title="Question", border_style="blue"))

    # Interroger le wiki
    engine = QAEngine()
    try:
        result = engine.query(args.question, max_sources=args.max_sources)
    except Exception as e:
        console.print(f"[bold red]❌ Erreur : {e}[/bold red]")
        return 1

    # Afficher la réponse
    console.print()
    console.print(Panel(result.answer, title="Réponse", border_style="green"))

    # Afficher les sources
    if result.sources:
        console.print("\n[bold]Sources utilisées :[/bold]")
        for src in result.sources:
            console.print(f"  • [[{src}]]")
    else:
        console.print("\n[dim]Aucune source trouvée.[/dim]")

    # Afficher les tokens si --verbose
    if args.verbose:
        console.print(
            f"\n[dim]Tokens — input: {result.input_tokens:,} | "
            f"output: {result.output_tokens:,} | "
            f"total: {result.input_tokens + result.output_tokens:,}[/dim]"
        )

    # Sauvegarder si --save
    if args.save:
        from src.qa.report_generator import ReportGenerator

        console.print("\n[dim]Sauvegarde du rapport...[/dim]")
        try:
            generator = ReportGenerator()
            report = generator.generate(args.question)
            console.print(f"[green]✅ Rapport sauvegardé : {report.output_path}[/green]")
        except Exception as e:
            console.print(f"[yellow]⚠️  Erreur lors de la sauvegarde : {e}[/yellow]")

    return 0


if __name__ == "__main__":
    sys.exit(main())
