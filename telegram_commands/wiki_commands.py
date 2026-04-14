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
    app.add_handler(CommandHandler("compile", cmd_compile))  # Phase 2
    app.add_handler(CommandHandler("ask", cmd_ask))  # Phase 3
    app.add_handler(CommandHandler("report", cmd_report))  # Phase 3
    app.add_handler(CommandHandler("slides", cmd_slides))  # Phase 3
    app.add_handler(CommandHandler("health", cmd_health))  # Phase 4
    app.add_handler(CommandHandler("search", cmd_search))  # Phase 5

    logger.info(
        "Handlers wiki Telegram enregistrés : "
        "/ingest, /status, /compile, /ask, /report, /slides, /health, /search"
    )


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
        vault = Path(settings.get_vault_path())

        if not vault.exists():
            await update.message.reply_text(f"❌ Vault introuvable : {settings.get_vault_path()}")
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
# Phase 2 — Compilation
# ---------------------------------------------------------------------------


async def cmd_compile(update: "Update", context: "ContextTypes.DEFAULT_TYPE") -> None:  # type: ignore[name-defined]
    """Handler /compile — compile le wiki via WikiCompiler.

    Usage: /compile [medium|substack|all]
    """
    assert update.message is not None

    args = context.args or []
    source = args[0] if args else "all"

    if source not in ("medium", "substack", "all"):
        await update.message.reply_text(
            "❌ Source invalide. Utilisation : /compile [medium|substack|all]"
        )
        return

    await update.message.reply_text(f"🔄 Compilation en cours (source: {source})...")

    try:
        from src.wiki.compiler import WikiCompiler

        compiler = WikiCompiler()
        result = compiler.batch_compile(source=source)

        msg = (
            f"✅ *Compilation terminée*\n\n"
            f"📊 Articles compilés : {result.total_compiled}/{result.total_articles}\n"
            f"⏭️  Ignorés : {result.total_skipped}\n"
            f"🆕 Fiches créées : {result.total_concepts_created}\n"
            f"🔄 Fiches mises à jour : {result.total_concepts_updated}\n"
        )
        if result.total_errors > 0:
            msg += f"⚠️  Erreurs : {result.total_errors}\n"

        await update.message.reply_text(msg, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Erreur /compile: {e}")
        await update.message.reply_text(f"❌ Erreur lors de la compilation : {e}")


# ---------------------------------------------------------------------------
# Phase 3 — Q&A et génération de contenu
# ---------------------------------------------------------------------------


async def cmd_ask(update: "Update", context: "ContextTypes.DEFAULT_TYPE") -> None:  # type: ignore[name-defined]
    """Handler /ask — Q&A sur le wiki via QAEngine.

    Usage: /ask <question>
    """
    assert update.message is not None

    args = context.args or []
    if not args:
        await update.message.reply_text(
            "❌ Usage : /ask <question>\nExemple : /ask Qu'est-ce que le RAG ?"
        )
        return

    question = " ".join(args)
    await update.message.reply_text(
        f"🔍 Recherche en cours pour : _{question}_", parse_mode="Markdown"
    )

    try:
        from src.qa.engine import QAEngine

        engine = QAEngine()
        result = engine.query(question)

        # Construire la réponse Telegram (limite 4096 chars)
        sources_text = ""
        if result.sources:
            sources_list = " | ".join(f"[[{s}]]" for s in result.sources[:5])
            sources_text = f"\n\n📚 *Sources* : {sources_list}"

        answer_truncated = result.answer
        max_answer_len = 4096 - len(sources_text) - 50
        if len(answer_truncated) > max_answer_len:
            answer_truncated = answer_truncated[:max_answer_len] + "\n\n_[réponse tronquée]_"

        msg = answer_truncated + sources_text
        await update.message.reply_text(msg, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Erreur /ask: {e}")
        await update.message.reply_text(f"❌ Erreur lors de la recherche : {e}")


async def cmd_report(update: "Update", context: "ContextTypes.DEFAULT_TYPE") -> None:  # type: ignore[name-defined]
    """Handler /report — génère un rapport Markdown sur un topic.

    Usage: /report <topic>
    """
    assert update.message is not None

    args = context.args or []
    if not args:
        await update.message.reply_text("❌ Usage : /report <topic>\nExemple : /report GraphRAG")
        return

    topic = " ".join(args)
    await update.message.reply_text(
        f"📄 Génération du rapport sur : _{topic}_...", parse_mode="Markdown"
    )

    try:
        from src.qa.report_generator import ReportGenerator

        generator = ReportGenerator()
        result = generator.generate(topic)

        msg = (
            f"✅ *Rapport généré*\n\n"
            f"📄 Fichier : `{result.output_path.name}`\n"
            f"📊 {result.word_count} mots, {result.sources_count} sources\n"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Erreur /report: {e}")
        await update.message.reply_text(f"❌ Erreur lors de la génération : {e}")


async def cmd_slides(update: "Update", context: "ContextTypes.DEFAULT_TYPE") -> None:  # type: ignore[name-defined]
    """Handler /slides — génère des slides Marp sur un topic.

    Usage: /slides <topic>
    """
    assert update.message is not None

    args = context.args or []
    if not args:
        await update.message.reply_text(
            "❌ Usage : /slides <topic>\nExemple : /slides Knowledge Graphs"
        )
        return

    topic = " ".join(args)
    await update.message.reply_text(
        f"🎯 Génération des slides sur : _{topic}_...", parse_mode="Markdown"
    )

    try:
        from src.qa.slide_generator import SlideGenerator

        generator = SlideGenerator()
        result = generator.generate(topic)

        msg = (
            f"✅ *Slides générées*\n\n"
            f"🎯 Fichier : `{result.output_path.name}`\n"
            f"📊 {result.slides_count} slides\n"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Erreur /slides: {e}")
        await update.message.reply_text(f"❌ Erreur lors de la génération : {e}")


# ---------------------------------------------------------------------------
# Phase 4 — Health check
# ---------------------------------------------------------------------------


async def cmd_health(update: "Update", context: "ContextTypes.DEFAULT_TYPE") -> None:  # type: ignore[name-defined]
    """Handler /health — health check du wiki via HealthChecker."""
    assert update.message is not None

    await update.message.reply_text("🔍 Analyse du wiki en cours...")

    try:
        from src.lint.health_checker import HealthChecker

        checker = HealthChecker()
        report = checker.run_full_check()

        score_emoji = "✅" if report.score >= 80 else ("⚠️" if report.score >= 50 else "❌")

        msg = (
            f"{score_emoji} *Health Check Wiki*\n\n"
            f"🏆 Score : *{report.score}/100*\n"
            f"📁 Fiches wiki : {report.total_wiki_fiches}\n\n"
            f"🔗 Liens cassés : {len(report.broken_links)}\n"
            f"👻 Concepts orphelins : {len(report.orphaned_concepts)}\n"
            f"🔄 Groupes de doublons : {len(report.duplicate_groups)}\n"
            f"📝 Définitions manquantes : {len(report.missing_definitions)}\n"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Erreur /health: {e}")
        await update.message.reply_text(f"❌ Erreur lors du health check : {e}")


# ---------------------------------------------------------------------------
# Phase 5 — Recherche (stub)
# ---------------------------------------------------------------------------


async def cmd_search(update: "Update", context: "ContextTypes.DEFAULT_TYPE") -> None:  # type: ignore[name-defined]
    """Handler /search — recherche full-text dans le vault via qmd.

    Usage:
        /search <query>              — recherche texte libre dans le wiki
        /search <query> -n 10        — nombre de résultats (défaut: 5)
        /search --concept <name>     — fichiers référençant [[name]]
    """
    assert update.message is not None

    import json
    import subprocess

    args = context.args or []
    if not args:
        await update.message.reply_text(
            "❌ Usage :\n"
            "  /search <requête>\n"
            "  /search <requête> -n 10\n"
            "  /search --concept <concept>\n\n"
            "Exemple : /search GraphRAG\n"
            "Exemple : /search RAG -n 10\n"
            "Exemple : /search --concept GraphRAG"
        )
        return

    # Parser les arguments
    concept: str | None = None
    query_parts: list[str] = []
    limit = 5

    i = 0
    while i < len(args):
        if args[i] == "--concept" and i + 1 < len(args):
            concept = args[i + 1]
            i += 1
        elif args[i] == "-n" and i + 1 < len(args):
            try:
                limit = int(args[i + 1])
                limit = max(1, min(20, limit))  # Clamp entre 1 et 20
            except ValueError:
                pass
            i += 1
        else:
            query_parts.append(args[i])
        i += 1

    # Mode --concept (recherche par backlink)
    if concept:
        await update.message.reply_text(f"🔍 Recherche des références à [[{concept}]]...")

        try:
            # Utiliser qmd search avec grep-like sur le concept
            result = subprocess.run(
                ["qmd", "search", f"[[{concept}]]", "-c", "wiki", "-n", "20", "--json"],
                capture_output=True,
                text=True,
                timeout=15,
            )

            if result.returncode != 0:
                await update.message.reply_text(f"❌ Erreur recherche : {result.stderr[:200]}")
                return

            hits = json.loads(result.stdout)
            if not hits:
                await update.message.reply_text(f"Aucun fichier référençant [[{concept}]].")
                return

            lines = [f"📎 *Fichiers référençant [[{concept}]]* ({len(hits)})\n"]
            for hit in hits[:15]:
                path = hit.get("path", "").replace("qmd://wiki/", "").replace(".md", "")
                title = hit.get("title", path)
                lines.append(f"• {title}")
            if len(hits) > 15:
                lines.append(f"_...et {len(hits) - 15} autres_")

            await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
            return

        except Exception as e:
            logger.error(f"Erreur /search --concept: {e}")
            await update.message.reply_text(f"❌ Erreur lors de la recherche : {e}")
            return

    # Mode texte libre
    if not query_parts:
        await update.message.reply_text("❌ Spécifiez une requête. Exemple : /search GraphRAG")
        return

    query = " ".join(query_parts)
    await update.message.reply_text(f"🔍 Recherche de _{query}_...", parse_mode="Markdown")

    try:
        # Appel qmd search
        result = subprocess.run(
            ["qmd", "search", query, "-c", "wiki", "-n", str(limit), "--json"],
            capture_output=True,
            text=True,
            timeout=15,
        )

        if result.returncode != 0:
            await update.message.reply_text(f"❌ Erreur recherche : {result.stderr[:200]}")
            return

        hits = json.loads(result.stdout)

        if not hits:
            await update.message.reply_text(f"Aucun résultat pour : {query!r}")
            return

        lines = [f"🔍 *{len(hits)} résultat(s)* pour _{query}_\n"]

        for i, hit in enumerate(hits[:10], 1):
            title = hit.get("title", "Sans titre")
            score = hit.get("score", 0)
            path = hit.get("path", "").replace("qmd://wiki/", "").replace(".md", "")
            snippet = hit.get("snippet", "")

            # Tronquer le snippet
            snippet_short = snippet[:100] + "..." if len(snippet) > 100 else snippet

            lines.append(f"{i}. *{title}* (score: {int(score * 100)}%)")
            if snippet_short:
                lines.append(f"   _{snippet_short}_")
            lines.append("")

        msg = "\n".join(lines)
        # Limiter à 4000 chars (limite Telegram avec marge)
        if len(msg) > 4000:
            msg = msg[:4000] + "\n_[tronqué]_"

        await update.message.reply_text(msg, parse_mode="Markdown")

    except subprocess.TimeoutExpired:
        await update.message.reply_text("⏱️ La recherche a pris trop de temps. Réessayez.")
    except json.JSONDecodeError as e:
        logger.error(f"JSON parse error: {e}")
        await update.message.reply_text("❌ Erreur de parsing des résultats.")
    except Exception as e:
        logger.error(f"Erreur /search: {e}")
        await update.message.reply_text(f"❌ Erreur lors de la recherche : {e}")
