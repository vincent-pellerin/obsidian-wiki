"""Handlers Telegram pour les commandes wiki.

Importé par second-brain-workflow/telegram_bot.py :

    from obsidian_wiki.telegram_commands.wiki_commands import register_wiki_handlers
    register_wiki_handlers(app)

Commandes disponibles:
    /ingest   — Lance les bridges (Medium, Substack)
    /compile  — Compile le wiki (Phase 2)
    /ask      — Q&A sur le wiki (Phase 3)
    /report   — Génère un rapport (Phase 3)
    /slides   — Génère une présentation Marp (Phase 3)
    /search   — Recherche dans le vault (Phase 5)
    /health   — Health check du wiki (Phase 4)
    /status   — Statistiques wiki
"""

import logging
import sys
from pathlib import Path

# Racine du projet obsidian-wiki
WIKI_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(WIKI_ROOT))

logger = logging.getLogger(__name__)

# Telegram Application type — import conditionnel pour éviter la dépendance
# si telegram_commands est utilisé sans python-telegram-bot installé
try:
    from telegram import Update
    from telegram.ext import Application, CommandHandler, ContextTypes

    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False
    logger.warning("python-telegram-bot non installé — commandes Telegram désactivées")


def register_wiki_handlers(app: "Application") -> None:  # type: ignore[name-defined]
    """Enregistre les handlers wiki dans l'application Telegram.

    À appeler depuis second-brain-workflow/telegram_bot.py après
    la création de l'Application :

        app = ApplicationBuilder().token(TOKEN).build()
        register_wiki_handlers(app)

    Args:
        app: Instance Application python-telegram-bot.

    Raises:
        ImportError: Si python-telegram-bot n'est pas installé.
    """
    if not TELEGRAM_AVAILABLE:
        raise ImportError(
            "python-telegram-bot requis pour les commandes Telegram. "
            "Installez-le dans second-brain-workflow : uv add python-telegram-bot"
        )

    app.add_handler(CommandHandler("ingest", cmd_ingest))
    app.add_handler(CommandHandler("status", cmd_status))
    # Phases suivantes — décommentez au fur et à mesure
    # app.add_handler(CommandHandler("compile", cmd_compile))   # Phase 2
    # app.add_handler(CommandHandler("ask", cmd_ask))           # Phase 3
    # app.add_handler(CommandHandler("report", cmd_report))     # Phase 3
    # app.add_handler(CommandHandler("slides", cmd_slides))     # Phase 3
    # app.add_handler(CommandHandler("search", cmd_search))     # Phase 5
    # app.add_handler(CommandHandler("health", cmd_health))     # Phase 4

    logger.info("Handlers wiki Telegram enregistrés : /ingest, /status")


# ---------------------------------------------------------------------------
# Phase 1 — Commandes disponibles
# ---------------------------------------------------------------------------


async def cmd_ingest(update: "Update", context: "ContextTypes.DEFAULT_TYPE") -> None:  # type: ignore[name-defined]
    """Handler /ingest — lance les bridges Medium et Substack.

    Usage: /ingest [medium|substack|all]
    """
    assert update.message is not None

    args = context.args or []
    source = args[0] if args else "all"

    if source not in ("medium", "substack", "all"):
        await update.message.reply_text(
            "❌ Source invalide. Utilisation : /ingest [medium|substack|all]"
        )
        return

    await update.message.reply_text(f"🔄 Ingestion en cours (source: {source})...")

    try:
        from bridges.medium_bridge import MediumBridge
        from bridges.substack_bridge import SubstackBridge

        lines: list[str] = []

        if source in ("medium", "all"):
            try:
                bridge = MediumBridge()
                result = bridge.sync_to_raw()
                lines.append(
                    f"📰 Medium: {len(result.synced)} copiés, {len(result.skipped)} ignorés"
                )
            except FileNotFoundError:
                lines.append("📰 Medium: source introuvable, ignoré")

        if source in ("substack", "all"):
            try:
                bridge_sub = SubstackBridge()
                result_sub = bridge_sub.sync_all()
                lines.append(
                    f"📧 Substack: {result_sub.total_synced} copiés, "
                    f"{result_sub.total_skipped} ignorés"
                )
            except FileNotFoundError:
                lines.append("📧 Substack: source introuvable, ignoré")

        await update.message.reply_text("✅ Ingestion terminée !\n" + "\n".join(lines))

    except Exception as e:
        logger.error(f"Erreur /ingest: {e}")
        await update.message.reply_text(f"❌ Erreur lors de l'ingestion : {e}")


async def cmd_status(update: "Update", context: "ContextTypes.DEFAULT_TYPE") -> None:  # type: ignore[name-defined]
    """Handler /status — affiche les statistiques du vault.

    Compte les fichiers dans chaque section du vault.
    """
    assert update.message is not None

    try:
        from src.config import get_settings

        settings = get_settings()
        vault = Path(settings.vault_path)

        if not vault.exists():
            await update.message.reply_text(f"❌ Vault introuvable : {settings.vault_path}")
            return

        def count_md(path: Path) -> int:
            return len(list(path.rglob("*.md"))) if path.exists() else 0

        raw_medium = count_md(vault / "00_RAW" / "articles" / "medium")
        raw_substack = count_md(vault / "00_RAW" / "articles" / "substack")
        wiki_total = count_md(vault / "02_WIKI")
        output_total = count_md(vault / "03_OUTPUT")

        msg = (
            "📊 *Statut du vault Obsidian*\n\n"
            f"📰 RAW Medium : {raw_medium} articles\n"
            f"📧 RAW Substack : {raw_substack} articles\n"
            f"🧠 Wiki : {wiki_total} fiches\n"
            f"📄 Outputs : {output_total} fichiers\n"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Erreur /status: {e}")
        await update.message.reply_text(f"❌ Erreur : {e}")


# ---------------------------------------------------------------------------
# Phases suivantes — stubs (non enregistrés)
# ---------------------------------------------------------------------------


async def cmd_compile(update: "Update", context: "ContextTypes.DEFAULT_TYPE") -> None:  # type: ignore[name-defined]
    """Handler /compile — compile le wiki (Phase 2, à implémenter)."""
    assert update.message is not None
    await update.message.reply_text("🚧 /compile disponible en Phase 2.")


async def cmd_ask(update: "Update", context: "ContextTypes.DEFAULT_TYPE") -> None:  # type: ignore[name-defined]
    """Handler /ask — Q&A sur le wiki (Phase 3, à implémenter)."""
    assert update.message is not None
    await update.message.reply_text("🚧 /ask disponible en Phase 3.")


async def cmd_health(update: "Update", context: "ContextTypes.DEFAULT_TYPE") -> None:  # type: ignore[name-defined]
    """Handler /health — health check du wiki (Phase 4, à implémenter)."""
    assert update.message is not None
    await update.message.reply_text("🚧 /health disponible en Phase 4.")


async def cmd_search(update: "Update", context: "ContextTypes.DEFAULT_TYPE") -> None:  # type: ignore[name-defined]
    """Handler /search — recherche dans le vault (Phase 5, à implémenter)."""
    assert update.message is not None
    await update.message.reply_text("🚧 /search disponible en Phase 5.")
