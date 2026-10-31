# AMBRE 0.1 — Où en est le plan (point de reprise)

> Mis à jour le 2026-10-31. Fichier temporaire, à supprimer une fois la v0.1 terminée.

## Où est le travail

- **Branche :** `feat/ambre-v0.1`. Rien n'est mergé dans `main`, qui ne contient que la spec et le plan.
- **Spec :** `docs/superpowers/specs/2026-10-31-ambre-master-spec.md`
- **Plan :** `docs/superpowers/plans/2026-10-31-ambre-v0.1-foundation.md`
- **Journal détaillé :** `.superpowers/sdd/2026-10-31-ambre-v0.1-foundation/progress.md`. Ce dossier est ignoré par git. Le journal contient chaque décision prise (les lignes `Ruling:`) et les points mineurs reportés. Il sert de source de vérité pour reprendre.

## Avancement : 10 tâches sur 12 terminées et relues

| # | Tâche | État | Commits |
|---|---|---|---|
| 1 | Socle et configuration | ✅ | 2a7c07a, 331c77a |
| 2 | Contrats du domaine | ✅ | 47ad147 |
| 3 | Calculs CPMM, EV et Kelly | ✅ | 64fd987 |
| 4 | Client Manifold (lecture seule) | ✅ | eac66d1 |
| 5 | Persistance et migration | ✅ | 88d9127, 714c4d1 |
| 6 | Synchronisation incrémentale | ✅ | 5b0ba13, 25edc05 |
| 7 | Features, baselines, stratégie, risque, survie | ✅ | 88cde29, 727a6ad |
| 8 | Portefeuille, exécution papier, règlement | ✅ | 3a976c4 |
| 9 | Pipeline de décision et CLI `ambre` | ✅ | 10c8ae6, 981c016 |
| 10 | API FastAPI | ✅ | 4e90594, 6be2aa5 |
| 11 | Dashboard Next.js (`apps/dashboard`) | ⏸ arrêtée en cours de route | rien de commité ; `apps/dashboard` contient un travail partiel non relu |
| 12 | Docker Compose, docs, validation finale | ⬜ à faire | |
| — | Relecture finale de toute la branche | ⬜ à faire | |

**Tests au dernier état commité (6be2aa5) :** 260 passent avec `-W error`, et ruff, ruff format et mypy (strict) sont propres.

## Ce qui marche déjà

Test réel en lecture seule sur `api.manifold.markets`, sur une base neuve :
- **Synchro :** 1 116 marchés stockés, dont 884 ignorés (marchés non binaires).
- **Premier cycle :** état BALANCED. 69 marchés évalués : 35 mises simulées, 18 refusées et 16 sans pari. 95 marchés ont été sautés faute de capacité.
- **Comptes :** cash + coût des positions = 1 000 exactement.
- **Second cycle :** aucun doublon créé.

## Pour reprendre

1. **Vérifier l'état de la Tâche 11.** Lancer `git status` et `git log --oneline -3`.
   - Si `apps/` n'est pas commité, c'est que la tâche a été interrompue. Le plus simple est de supprimer `apps/dashboard` et de relancer la Tâche 11.
   - Si un commit `feat(dashboard)` existe, passer directement à sa relecture.
2. **Relancer l'exécution.** Dans Claude Code, demander :
   « Reprends l'exécution du plan `docs/superpowers/plans/2026-10-31-ambre-v0.1-foundation.md` avec superpowers:subagent-driven-development, à partir de la Tâche 11 (le journal est dans `.superpowers/sdd/...`). »
   Le skill relit le journal et ne refait pas les tâches déjà marquées `complete`.
3. **Dans la Tâche 12**, le CHANGELOG doit reprendre les chiffres du test réel ci-dessus, ainsi que les problèmes connus. Les principaux :
   - Postgres n'a pas été testé (Docker absent) ;
   - la simulation CPMM ignore les ordres limites ;
   - la baseline n'est pas validée ;
   - la synchro passe par du polling, pas par WebSocket.
4. **Après la relecture finale**, donner la liste complète des décisions prises (`Ruling:`) avec leur coût si elles sont fausses, puis proposer le merge.

## Commandes utiles

```bash
uv run pytest -q                      # tests backend
uv run ambre init-db                  # crée var/ambre.db
uv run ambre sync --max-pages 2       # lecture Manifold
uv run ambre cycle                    # un cycle de décision papier
uv run uvicorn ambre.api.app:app --port 8000
```
