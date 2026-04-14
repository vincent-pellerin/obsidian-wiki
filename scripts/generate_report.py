"""Script de génération de rapports et slides.

Usage:
    uv run python scripts/generate_report.py "GraphRAG"
    uv run python scripts/generate_report.py "Knowledge Graphs" --slides
    uv run python scripts/generate_report.py "RAG" --slides --report
"""

import argparse
import logging
import sys
from pathlib import Path

# Ajouter la racine du projet au PYTHONPATH
sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console

from src.config import get_settings

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
        description="Génère des rapports Markdown et/ou des slides Marp sur un topic",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples:
  uv run python scripts/generate_report.py "GraphRAG"
  uv run python scripts/generate_report.py "Knowledge Graphs" --slides
  uv run python scripts/generate_report.py "RAG" --slides --report
        """,
    )
    parser.add_argument(
        "topic",
        help="Sujet du rapport ou de la présentation",
    )
    parser.add_argument(
        "--slides",
        action="store_true",
        help="Générer aussi des slides Marp",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="Générer un rapport Markdown (défaut si --slides non spécifié)",
    )
    return parser.parse_args()


def main() -> int:
    """Point d'entrée du script de génération.

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
        console.print("Ajoutez GEMINI_API_KEY ou GOOGLE_API_KEY dans .env ou ~/.zshenv")
        return 1

    console.print("[bold]🧠 obsidian-wiki — Génération[/bold]")
    console.print(f"[dim]Vault : {settings.get_vault_path()}[/dim]")
    console.print(f"[dim]Topic : {args.topic}[/dim]\n")

    # Par défaut, générer un rapport si --slides n'est pas le seul flag
    generate_report = args.report or not args.slides
    generate_slides = args.slides

    exit_code = 0

    # Génération du rapport
    if generate_report:
        console.print("[dim]📄 Génération du rapport...[/dim]")
        try:
            from src.qa.report_generator import ReportGenerator

            generator = ReportGenerator()
            result = generator.generate(args.topic)
            console.print(f"[green]✅ Rapport généré : {result.output_path}[/green]")
            console.print(f"[dim]   {result.word_count} mots, {result.sources_count} sources[/dim]")
        except Exception as e:
            console.print(f"[bold red]❌ Erreur rapport : {e}[/bold red]")
            exit_code = 1

    # Génération des slides
    if generate_slides:
        console.print("[dim]🎯 Génération des slides...[/dim]")
        try:
            from src.qa.slide_generator import SlideGenerator

            generator_slides = SlideGenerator()
            result_slides = generator_slides.generate(args.topic)
            console.print(f"[green]✅ Slides générées : {result_slides.output_path}[/green]")
            console.print(f"[dim]   {result_slides.slides_count} slides[/dim]")
        except Exception as e:
            console.print(f"[bold red]❌ Erreur slides : {e}[/bold red]")
            exit_code = 1

    if exit_code == 0:
        console.print("\n[bold green]✅ Génération terminée.[/bold green]")
    else:
        console.print("\n[bold yellow]⚠️  Terminé avec des erreurs.[/bold yellow]")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
