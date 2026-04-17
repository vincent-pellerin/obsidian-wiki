# TODO

## Optimisation coûts API — Mode Batch Gemini

**Priorité** : basse (économie ~$1.55 pour le batch complet)

### Contexte

Le script `compile_wiki.py` utilise actuellement l'API Gemini en mode **Standard** (appels synchrones séquentiels). Google propose un mode **Lot (Batch)** 2× moins cher.

| Mode | Input | Output | Délai |
|------|-------|--------|-------|
| Standard (actuel) | $0.10/M tokens | $0.40/M tokens | Immédiat (~3s/article) |
| Lot (Batch) | $0.05/M tokens | $0.20/M tokens | Jusqu'à 24h |

Source : [ai.google.dev/gemini-api/docs/pricing](https://ai.google.dev/gemini-api/docs/pricing)

### Estimation sur 5 740 articles

- Standard : ~$3.10
- Batch : ~**$1.55**
- Économie : ~$1.55

### Changement requis dans le code

Remplacer `client.models.generate_content()` par l'API batch :

```python
# Soumettre tous les prompts en une seule requête
batch_job = client.batches.create(model=..., requests=[...])

# Poller jusqu'à completion
while job.state == "JOB_STATE_RUNNING":
    time.sleep(60)
    job = client.batches.get(name=batch_job.name)
```

### Impact architecture

- Collecter tous les articles avant d'envoyer (pas de traitement au fil de l'eau)
- Pas de progression en temps réel
- Résultats disponibles après quelques heures
- Idéal pour un batch overnight sur la totalité des articles

---

## Post-compilation — Nettoyage des liens cassés

**Priorité** : haute (à faire dès que la compilation en cours est terminée)

### Contexte

Des fichiers ont été supprimés dans `00_RAW/`. Les fiches wiki dans `02_WIKI/` référencent encore ces articles supprimés via `[[wikilinks]]` et les `source_count` sont gonflés. Il faut nettoyer ces références orphelines.

### État actuel

- `qmd update` + `qmd embed` ✅ faits — l'index de recherche est propre
- `compile_wiki.py` 🔄 en cours — première compilation complète (5 053 articles non compilés sur 5 126)
- Nettoyage des liens cassés ❌ pas encore fait

### Commandes à exécuter

```bash
# 1. Lint pour détecter les liens cassés
uv run python scripts/lint_wiki.py --report

# 2. Fix automatique si supporté
uv run python scripts/lint_wiki.py --fix

# 3. Re-indexer qmd après les modifications de fiches
qmd update && qmd embed
```

### Note sur compile_wiki.py

`compile_wiki.py` est **incrémental** : il skip les articles déjà marqués `wiki_compiled: true` dans le frontmatter. Il ne traite que les nouveaux articles. En cas de **suppression** d'articles RAW, il n'y a rien à recompiler — mais les fiches wiki orphelines doivent être nettoyées via `lint_wiki.py`.
