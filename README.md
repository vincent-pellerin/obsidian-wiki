# obsidian-wiki

Système de knowledge base structuré et auto-maintenu par LLM pour le vault Obsidian.

> "The LLM writes and maintains all of the data of the wiki, I rarely touch it directly."
> — Andrej Karpathy

---

## Rôle dans l'écosystème second brain

Ce projet est la couche **organisation et exploitation** du second brain, clairement séparé de l'ingestion :

| Projet | Rôle | Responsabilité |
|--------|------|----------------|
| [`second-brain-workflow`](../second-brain-workflow/) | **Ingestion** | Extraction YouTube/articles, bot Telegram, notes brutes → Obsidian |
| **`obsidian-wiki`** | **Organisation** | Compilation wiki, Q&A, health checks, visualisations |

Les deux projets partagent le même vault Obsidian via **Syncthing** (VPS → local).

---

## Fonctionnalités

- **Bridges** : synchronisation Medium/Substack → `00_RAW/` du vault
- **Wiki Compiler** : transformation des articles bruts en fiches concepts structurées
- **Q&A Engine** : réponses basées sur le wiki avec citations de sources
- **Health Checker** : détection des liens cassés, concepts orphelins, doublons
- **Search** : recherche full-text via [qmd](https://github.com/tobi/qmd) (BM25 + hybride)
- **Generators** : rapports Markdown, présentations Marp, graphiques de concepts

---

## Architecture du projet

```
obsidian-wiki/
├── bridges/                    # Connecteurs vers les sources externes
│   ├── __init__.py
│   ├── medium_bridge.py        # medium_extract/output/ → 00_RAW/articles/medium/
│   ├── substack_bridge.py      # substack_extract/output/ → 00_RAW/articles/substack/
│   └── web_clipper.py          # API pour Web Clipper Obsidian
│
├── src/
│   ├── wiki/                   # Cœur du système wiki
│   │   ├── __init__.py
│   │   ├── compiler.py         # Compilation articles → fiches concepts
│   │   ├── concept_manager.py  # CRUD fiches concepts
│   │   ├── linker.py           # Gestion backlinks bidirectionnels
│   │   ├── indexer.py          # Génération index maître
│   │   └── utils.py
│   │
│   ├── qa/                     # Module Q&A et génération
│   │   ├── __init__.py
│   │   ├── engine.py           # Moteur questions/réponses
│   │   ├── report_generator.py # Rapports Markdown
│   │   ├── slide_generator.py  # Présentations Marp
│   │   └── graph_generator.py  # Visualisations réseau
│   │
│   └── lint/                   # Health checker
│       ├── __init__.py
│       ├── health_checker.py   # Vérifications qualité wiki
│       └── enricher.py         # Enrichissement automatique
│
├── scripts/                    # Scripts CLI autonomes
│   ├── ingest_all.py           # Lance tous les bridges
│   ├── compile_wiki.py         # Compilation wiki
│   ├── ask_wiki.py             # Q&A en ligne de commande
│   ├── lint_wiki.py            # Health check
│   └── generate_report.py      # Génération rapport
│
├── pyproject.toml
├── .env.example
└── README.md

# Outil externe (binaire système, pas une dépendance Python)
# qmd — https://github.com/tobi/qmd
#   CLI : qmd search "query" -c wiki
#   MCP : qmd mcp (serveur MCP natif)
```

---

## Vault Obsidian — Structure cible

```
obsidian-second-brain-vps/      # Sur VPS, synchro Syncthing → local
├── 00_RAW/                     # Données brutes (jamais modifiées manuellement)
│   ├── articles/
│   │   ├── medium/
│   │   ├── substack/
│   │   └── web/
│   ├── youtube/transcripts/
│   └── images/
├── 01_INBOX/                   # File d'attente de traitement
├── 02_WIKI/                    # Wiki compilé par LLM (auto-maintenu)
│   ├── Concepts/
│   ├── People/
│   ├── Technologies/
│   ├── Topics/
│   └── Index/
├── 03_OUTPUT/                  # Réponses et livrables générés
│   ├── Reports/
│   ├── Slides/
│   └── Graphs/
└── 04_ARCHIVE/
```

---

## Déploiement

> ⚠️ **Ce projet s'exécute sur le VPS** (`vps_new` — `159.69.4.64`).
> Les modifications du vault Obsidian sont propagées automatiquement vers les instances
> locales (Mac, mobile) via **Syncthing** (conteneur Docker, port 22000).

```bash
# Connexion au VPS
ssh vps_new

# Emplacement du projet
cd ~/dev/obsidian-wiki/

# Installation des dépendances Python
uv sync

# --- Phase 1 : Ingestion ---
uv run python scripts/ingest_all.py

# --- Phase 2 : Compilation ---
uv run python scripts/compile_wiki.py --async --concurrency 5

# Mettre à jour l'index qmd après compilation
qmd update

# --- Phase 3 : Q&A ---
uv run python scripts/ask_wiki.py "Qu'est-ce que GraphRAG ?"

# --- Phase 4 : Health Check ---
uv run python scripts/lint_wiki.py --report

# --- Phase 5 : Recherche via qmd ---
qmd search "RAG" -c wiki -n 10           # Recherche rapide (BM25)
qmd query "comment déployer" -c wiki     # Recherche hybride (meilleure qualité)
```

---

## Configuration

```env
# .env (copier depuis .env.example)
VAULT_PATH=/home/vincent/obsidian-second-brain-vps
GEMINI_API_KEY=...
GEMINI_MODEL_WIKI=gemini-2.5-flash
```

### Dépendances système

Le projet utilise **qmd** comme moteur de recherche externe (binaire Node.js, non une dépendance Python) :

```bash
# Installation sur le VPS
npm install -g @tobilu/qmd

# Configuration initiale (à faire une fois)
qmd collection add /home/vincent/obsidian-second-brain-vps/02_WIKI --name wiki

# Mise à jour de l'index après compilation
qmd update
```

**Architecture de la recherche :**
- `qmd search` — Recherche BM25 full-text (rapide)
- `qmd query` — Recherche hybride BM25 + vectoriel + reranking (meilleure qualité)
- Index stocké dans `~/.cache/qmd/index.sqlite`

---

## Documentation

Plan d'implémentation détaillé : [`~/dev/DOCS/obsidian-wiki/`](../DOCS/obsidian-wiki/)
