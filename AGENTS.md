# AGENTS.md — Wiki Schema

> Ce fichier est lu en premier par l'agent à chaque session.
> Il définit les règles, la structure et les opérations du vault wiki.

---

## Structure du vault

```
obsidian-second-brain-vps/
├── 00_RAW/                     # Données brutes — IMMUTABLE, ne jamais modifier
│   ├── articles/
│   │   ├── medium/              # Articles Medium extraits
│   │   ├── substack/            # Articles Substack extraits
│   │   └── web/                 # Articles web (Web Clipper)
│   ├── youtube/transcripts/     # Transcripts bruts YouTube
│   ├── papers/                  # Papers PDF (futur)
│   └── images/                  # Images téléchargées
│
├── 01_INBOX/                    # File d'attente de traitement
│
├── 02_WIKI/                     # Wiki compilé par LLM (auto-maintenu)
│   ├── Concepts/                # Fiches concepts
│   ├── People/                  # Fiches personnes
│   ├── Technologies/            # Fiches technologies
│   ├── Topics/                  # Fiches sujets
│   ├── Index/                   # Index maître
│   └── log.md                   # Journal des opérations (append-only)
│
├── 03_OUTPUT/                   # Réponses et livrables générés
│   ├── Reports/                 # Rapports Markdown
│   ├── Slides/                  # Présentations Marp
│   └── Graphs/                  # Visualisations réseau
│
├── 04_ARCHIVE/                  # Archives
└── AGENTS.md                    # Ce fichier — schema LLM du vault
```

---

## Format des fiches wiki

Chaque fiche dans `02_WIKI/` suit ce format obligatoire :

```markdown
---
title: "Nom de la fiche"
type: concept | person | technology | topic
aliases: ["Nom alternatif 1", "Nom alternatif 2"]
category: "Catégorie principale"
created: 2026-04-14
updated: 2026-04-14
source_count: 3
---

# Nom de la fiche

## Définition
Description concise du concept, personne, technologie ou sujet.

## Sources mentionnant ce concept
- [[article-id-1]]
- [[article-id-2]]

## Concepts liés
- [[Concept apparenté 1]] — Nature de la relation
- [[Concept apparenté 2]] — Nature de la relation

## Questions ouvertes
- Question non résolue ou point à approfondir
```

### Règles de format

- **Frontmatter YAML obligatoire** : `title`, `type`, `updated`, `source_count`
- **Sections attendues** : Définition, Sources, Concepts liés
- **Wikilinks `[[concept]]`** : Toujours utiliser le stem du fichier (sans extension)
- **`source_count`** : Nombre d'articles RAW référençant cette fiche
- **`updated`** : Date ISO de dernière modification

---

## Règles du vault

### Règles absolues

1. **`00_RAW/` est immutable** — Ne jamais modifier les fichiers sources directement
2. **Toute réponse utile** peut être sauvegardée dans `03_OUTPUT/`
3. **Mettre à jour `log.md`** à chaque opération (ingest, compile, lint, query)
4. **Mettre à jour l'index** après chaque compilation (`Index/000_Master_Index.md`)
5. **Backlinks bidirectionnels** : Si A mentionne B, alors B référence A

### Règles de nommage

- Fichiers wiki : `PascalCase.md` ou `kebab-case.md` (stem = nom du concept)
- Fichiers RAW : Garder le nom original de la source
- Rapports : `YYYY-MM-DD-topic-slug.md`
- Slides : `YYYY-MM-DD-topic-slug-slides.md`

---

## Opérations disponibles

### Ingest — Ingestion des sources

```bash
uv run python scripts/ingest_all.py
uv run python scripts/ingest_all.py --source medium
uv run python scripts/ingest_all.py --source substack
```

Copie les articles depuis les extracteurs vers `00_RAW/`. Déduplication automatique.

### Compile — Compilation wiki

```bash
uv run python scripts/compile_wiki.py --async                                    # Mode recommandé (concurrency=5)
uv run python scripts/compile_wiki.py --async --concurrency 5                    # Explicite
uv run python scripts/compile_wiki.py --async --source medium --limit 10
uv run python scripts/compile_wiki.py --async --force
uv run python scripts/compile_wiki.py --async --model gemini-2.5-flash-lite
uv run python scripts/compile_wiki.py --stats                                    # Stats uniquement (pas de compilation)
```

Transforme les articles RAW en fiches wiki via Gemini. Met à jour l'index et le log.
Mode `--async` recommandé : 15 requêtes simultanées par défaut, `--concurrency 5` pour limiter le rate limit.

### Query — Questions/Réponses

```bash
uv run python scripts/ask_wiki.py "Qu'est-ce que GraphRAG ?"
uv run python scripts/ask_wiki.py "Explique le RAG" --verbose
uv run python scripts/ask_wiki.py "Topic X" --save
```

Recherche via qmd (BM25 + hybride), puis synthèse par Gemini avec citations.

### Search — Recherche full-text

```bash
qmd search "RAG" -c wiki -n 10           # Recherche rapide (BM25)
qmd query "comment déployer" -c wiki     # Recherche hybride (meilleure qualité)
```

Moteur de recherche externe (binaire Go, pas une dépendance Python).

### Lint — Health check

```bash
uv run python scripts/lint_wiki.py
uv run python scripts/lint_wiki.py --report
uv run python scripts/lint_wiki.py --fix
uv run python scripts/lint_wiki.py --enrich GraphRAG
```

Vérifie la qualité du wiki : liens cassés, concepts orphelins, doublons, définitions manquantes.

### Rapports et slides

```bash
uv run python scripts/generate_report.py "GraphRAG"
uv run python scripts/generate_report.py "Knowledge Graphs" --slides
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

- **qmd** : Moteur de recherche (`npm install -g @tobilu/qmd` ou `go install github.com/tobi/qmd@latest`)
- **Python 3.12+** avec `uv`
- **Gemini API** (Google AI)

---

## Architecture

```
obsidian-wiki/
├── bridges/                    # Connecteurs vers les sources externes
│   ├── medium_bridge.py
│   └── substack_bridge.py
├── src/
│   ├── wiki/                   # Cœur du système wiki
│   │   ├── compiler.py         # Compilation articles → fiches concepts
│   │   ├── concept_manager.py  # CRUD fiches concepts
│   │   ├── linker.py           # Backlinks bidirectionnels
│   │   ├── indexer.py          # Génération index maître
│   │   └── cache.py            # Cache persistant (.wiki_state.json)
│   ├── qa/                     # Module Q&A et génération
│   │   ├── engine.py           # Moteur questions/réponses (qmd + Gemini)
│   │   ├── report_generator.py
│   │   └── slide_generator.py
│   └── lint/                   # Health checker
│       ├── health_checker.py
│       └── enricher.py
└── scripts/                    # Scripts CLI autonomes
    ├── ingest_all.py
    ├── compile_wiki.py
    ├── ask_wiki.py
    ├── lint_wiki.py
    └── generate_report.py
```

---

## Convention log.md

Le fichier `02_WIKI/log.md` est un journal append-only des opérations :

```markdown
## [2026-04-14] compile | Batch 42 articles
- Source : medium
- Fiches créées : 15, mises à jour : 27
- Erreurs : 0
- Tokens : 125 000 input / 45 000 output

## [2026-04-14] lint | Health Check
- Score : 87/100
- Liens cassés : 2
- Concepts orphelins : 1
```

Format : `## [YYYY-MM-DD] opération | titre` — parseable par `grep`.