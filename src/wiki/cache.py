"""Cache persistant pour l'état du wiki.

Maintient un fichier `.wiki_state.json` dans le vault avec :
- État de compilation par article (mtime, hash, wiki_compiled, concepts)
- Index des fiches wiki (stem → path relatif, type, source_count)
- Index inversé des backlinks (concept_stem → [article_stems])

Élimine les scans complets + parsing frontmatter répétés qui deviennent
prohibitifs quand le vault dépasse 400K mots (~5000+ fichiers).
"""

import hashlib
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Nom du fichier cache dans le vault
CACHE_FILENAME = ".wiki_state.json"

# Version du schéma cache (pour migration future)
CACHE_VERSION = 1


def _compute_content_hash(file_path: Path) -> str:
    """Calcule un hash MD5 rapide du contenu d'un fichier.

    Args:
        file_path: Chemin du fichier à hasher.

    Returns:
        Hash hexadécimal (32 chars).
    """
    try:
        content = file_path.read_bytes()
        return hashlib.md5(content).hexdigest()
    except OSError:
        return ""


class WikiStateCache:
    """Cache persistant pour l'état du vault wiki.

    Stocke les métadonnées dans un fichier JSON pour éviter de rescanner
    et parser le frontmatter de milliers de fichiers à chaque opération.

    Attributes:
        vault_path: Chemin racine du vault Obsidian.
        cache_path: Chemin du fichier `.wiki_state.json`.
    """

    def __init__(self, vault_path: Path) -> None:
        """Initialise le cache pour un vault donné.

        Args:
            vault_path: Chemin racine du vault Obsidian.
        """
        self.vault_path = vault_path
        self.cache_path = vault_path / CACHE_FILENAME
        self._data: dict = self._load()

    def _load(self) -> dict:
        """Charge le cache depuis le disque ou retourne un cache vide.

        Returns:
            Dictionnaire avec les données du cache.
        """
        if not self.cache_path.exists():
            return self._empty_cache()

        try:
            raw = self.cache_path.read_text(encoding="utf-8")
            data = json.loads(raw)
            if data.get("version") != CACHE_VERSION:
                logger.info(
                    f"Cache version mismatch ({data.get('version')} != {CACHE_VERSION}), "
                    "reconstruction..."
                )
                return self._empty_cache()
            return data
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Cache corrompu, reconstruction : {e}")
            return self._empty_cache()

    def _empty_cache(self) -> dict:
        """Retourne un cache vide avec la structure initiale.

        Returns:
            Dictionnaire cache vide.
        """
        return {
            "version": CACHE_VERSION,
            "articles": {},
            "wiki_fiches": {},
            "backlinks": {},
        }

    def save(self) -> None:
        """Persiste le cache sur disque."""
        try:
            content = json.dumps(self._data, ensure_ascii=False, indent=2)
            self.cache_path.write_text(content, encoding="utf-8")
        except OSError as e:
            logger.error(f"Impossible de sauvegarder le cache : {e}")

    # ------------------------------------------------------------------
    # Articles (état de compilation)
    # ------------------------------------------------------------------

    def get_article_state(self, article_path: Path) -> dict | None:
        """Retourne l'état d'un article depuis le cache.

        Args:
            article_path: Chemin absolu de l'article RAW.

        Returns:
            Dict avec mtime, content_hash, wiki_compiled, concepts,
            ou None si absent du cache.
        """
        key = str(article_path.relative_to(self.vault_path))
        return self._data["articles"].get(key)

    def set_article_state(
        self,
        article_path: Path,
        *,
        wiki_compiled: bool,
        concepts: list[str] | None = None,
    ) -> None:
        """Met à jour l'état d'un article dans le cache.

        Args:
            article_path: Chemin absolu de l'article RAW.
            wiki_compiled: Si l'article a été compilé.
            concepts: Liste des noms de concepts extraits.
        """
        key = str(article_path.relative_to(self.vault_path))
        try:
            stat = article_path.stat()
            mtime = stat.st_mtime
        except OSError:
            mtime = 0.0

        self._data["articles"][key] = {
            "mtime": mtime,
            "content_hash": _compute_content_hash(article_path),
            "wiki_compiled": wiki_compiled,
            "concepts": concepts or [],
        }

    def is_article_modified(self, article_path: Path) -> bool:
        """Vérifie si un article a été modifié depuis le dernier cache.

        Compare le mtime puis le hash de contenu en cas de doute.

        Args:
            article_path: Chemin absolu de l'article RAW.

        Returns:
            True si le fichier a changé ou n'est pas dans le cache.
        """
        state = self.get_article_state(article_path)
        if state is None:
            return True

        try:
            current_mtime = article_path.stat().st_mtime
        except OSError:
            return True

        # Fast path : même mtime = pas modifié
        if current_mtime == state.get("mtime", 0):
            return False

        # mtime différent : vérifier le hash (le mtime peut changer sans modif de contenu)
        current_hash = _compute_content_hash(article_path)
        return current_hash != state.get("content_hash", "")

    def get_compilation_stats_from_cache(self) -> dict:
        """Retourne les statistiques de compilation depuis le cache.

        Beaucoup plus rapide que scanner + parser tous les fichiers.

        Returns:
            Dict avec total_cached, total_compiled, pending_count.
        """
        articles = self._data.get("articles", {})
        total = len(articles)
        compiled = sum(1 for a in articles.values() if a.get("wiki_compiled"))
        return {
            "total_cached": total,
            "total_compiled": compiled,
            "pending_count": total - compiled,
        }

    # ------------------------------------------------------------------
    # Fiches wiki (index stem → métadonnées)
    # ------------------------------------------------------------------

    def set_fiche_state(
        self,
        fiche_path: Path,
        *,
        wiki_type: str,
        source_count: int,
        title: str = "",
    ) -> None:
        """Met à jour l'état d'une fiche wiki dans le cache.

        Args:
            fiche_path: Chemin absolu de la fiche wiki.
            wiki_type: Type de fiche (concept, person, technology, topic).
            source_count: Nombre de sources référençant cette fiche.
            title: Titre de la fiche.
        """
        stem = fiche_path.stem
        rel_path = str(fiche_path.relative_to(self.vault_path))
        self._data["wiki_fiches"][stem] = {
            "path": rel_path,
            "type": wiki_type,
            "source_count": source_count,
            "title": title or stem,
        }

    def get_fiche_state(self, stem: str) -> dict | None:
        """Retourne l'état d'une fiche wiki depuis le cache.

        Args:
            stem: Nom du fichier sans extension.

        Returns:
            Dict avec path, type, source_count, title, ou None.
        """
        return self._data["wiki_fiches"].get(stem)

    def get_fiche_path(self, stem: str) -> Path | None:
        """Retourne le chemin absolu d'une fiche depuis le cache.

        Args:
            stem: Nom du fichier sans extension.

        Returns:
            Chemin absolu ou None si absent du cache.
        """
        state = self.get_fiche_state(stem)
        if state is None:
            return None
        return self.vault_path / state["path"]

    def get_all_fiche_stems(self) -> set[str]:
        """Retourne l'ensemble des stems de fiches wiki connues.

        Returns:
            Set des stems de fiches.
        """
        return set(self._data["wiki_fiches"].keys())

    def get_total_wiki_fiches(self) -> int:
        """Retourne le nombre total de fiches wiki dans le cache.

        Returns:
            Nombre de fiches.
        """
        return len(self._data["wiki_fiches"])

    # ------------------------------------------------------------------
    # Backlinks (index inversé)
    # ------------------------------------------------------------------

    def add_backlink(self, concept_stem: str, article_stem: str) -> None:
        """Ajoute un backlink concept → article dans l'index inversé.

        Args:
            concept_stem: Stem de la fiche concept.
            article_stem: Stem de l'article source.
        """
        backlinks = self._data["backlinks"]
        if concept_stem not in backlinks:
            backlinks[concept_stem] = []
        if article_stem not in backlinks[concept_stem]:
            backlinks[concept_stem].append(article_stem)

    def get_backlinks(self, concept_stem: str) -> list[str]:
        """Retourne les stems des articles référençant un concept.

        Args:
            concept_stem: Stem de la fiche concept.

        Returns:
            Liste des stems d'articles.
        """
        return self._data["backlinks"].get(concept_stem, [])

    def set_backlinks(self, concept_stem: str, article_stems: list[str]) -> None:
        """Remplace les backlinks d'un concept.

        Args:
            concept_stem: Stem de la fiche concept.
            article_stems: Liste complète des stems d'articles.
        """
        self._data["backlinks"][concept_stem] = list(article_stems)

    # ------------------------------------------------------------------
    # Reconstruction complète
    # ------------------------------------------------------------------

    def rebuild_articles_index(self, raw_root: Path) -> int:
        """Reconstruit l'index des articles depuis le disque.

        Scanne tous les .md dans 00_RAW/ et lit le frontmatter pour
        déterminer l'état de compilation. Opération lente mais nécessaire
        uniquement au premier lancement ou si le cache est corrompu.

        Args:
            raw_root: Chemin vers 00_RAW/.

        Returns:
            Nombre d'articles indexés.
        """
        import frontmatter as fm

        if not raw_root.exists():
            return 0

        count = 0
        for md_file in raw_root.rglob("*.md"):
            try:
                post = fm.load(str(md_file))
                compiled = bool(post.metadata.get("wiki_compiled", False))
                self.set_article_state(md_file, wiki_compiled=compiled)
                count += 1
            except Exception as e:
                logger.debug(f"Erreur indexation {md_file.name} : {e}")

        logger.info(f"Index articles reconstruit : {count} fichiers")
        return count

    def rebuild_fiches_index(self, wiki_root: Path) -> int:
        """Reconstruit l'index des fiches wiki depuis le disque.

        Scanne tous les .md dans 02_WIKI/ et lit le frontmatter.

        Args:
            wiki_root: Chemin vers 02_WIKI/.

        Returns:
            Nombre de fiches indexées.
        """
        import frontmatter as fm

        if not wiki_root.exists():
            return 0

        self._data["wiki_fiches"] = {}
        count = 0
        for md_file in wiki_root.rglob("*.md"):
            if md_file.stem.startswith("000_"):
                continue
            try:
                post = fm.load(str(md_file))
                wiki_type = str(post.metadata.get("type", "concept"))
                source_count = int(post.metadata.get("source_count", 0))
                title = str(post.metadata.get("title", md_file.stem))
                self.set_fiche_state(
                    md_file,
                    wiki_type=wiki_type,
                    source_count=source_count,
                    title=title,
                )
                count += 1
            except Exception as e:
                logger.debug(f"Erreur indexation fiche {md_file.name} : {e}")

        logger.info(f"Index fiches reconstruit : {count} fiches")
        return count

    def rebuild_backlinks_index(self, wiki_root: Path) -> int:
        """Reconstruit l'index inversé des backlinks depuis le disque.

        Scanne les sections "## Sources" de toutes les fiches wiki
        pour extraire les liens [[article]].

        Args:
            wiki_root: Chemin vers 02_WIKI/.

        Returns:
            Nombre de backlinks indexés.
        """
        import re

        if not wiki_root.exists():
            return 0

        self._data["backlinks"] = {}
        wikilink_re = re.compile(r"\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]")
        count = 0

        for md_file in wiki_root.rglob("*.md"):
            if md_file.stem.startswith("000_"):
                continue
            try:
                content = md_file.read_text(encoding="utf-8")
                for match in wikilink_re.finditer(content):
                    target = match.group(1).strip()
                    if target:
                        self.add_backlink(md_file.stem, target)
                        count += 1
            except OSError as e:
                logger.debug(f"Erreur lecture backlinks {md_file.name} : {e}")

        logger.info(f"Index backlinks reconstruit : {count} liens")
        return count

    def rebuild_all(self) -> None:
        """Reconstruit tous les index depuis le disque.

        Opération lente — à utiliser uniquement au premier lancement
        ou pour réparer un cache corrompu.
        """
        raw_root = self.vault_path / "00_RAW"
        wiki_root = self.vault_path / "02_WIKI"

        logger.info("Reconstruction complète du cache wiki...")
        self._data = self._empty_cache()
        self.rebuild_articles_index(raw_root)
        self.rebuild_fiches_index(wiki_root)
        self.rebuild_backlinks_index(wiki_root)
        self.save()
        logger.info("Cache wiki reconstruit et sauvegardé.")

    def is_empty(self) -> bool:
        """Vérifie si le cache est vide (premier lancement).

        Returns:
            True si aucun article ni fiche n'est indexé.
        """
        return (
            len(self._data.get("articles", {})) == 0 and len(self._data.get("wiki_fiches", {})) == 0
        )
