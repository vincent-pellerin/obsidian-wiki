"""Commandes Telegram pour le wiki Obsidian.

Ces handlers sont importés par second-brain-workflow/telegram_bot.py.
"""

from telegram_commands.wiki_commands import register_wiki_handlers

__all__ = ["register_wiki_handlers"]
