# AMBRE 0.1 — Où en est le plan (point de reprise)

> Mis à jour le 2026-10-31. Fichier temporaire, à supprimer une fois la v0.1 mergée.

## Où est le travail

- **Branche :** `feat/dashboard-and-docs` (Tâches 11 et 12), basée sur `main`, qui contient déjà les Tâches 1 à 10. Rien n'est poussé.
- **Spec :** `docs/superpowers/specs/2026-10-31-ambre-master-spec.md`
- **Plan :** `docs/superpowers/plans/2026-10-31-ambre-v0.1-foundation.md`
- **Journal détaillé des décisions (`Ruling:`) :** `.superpowers/sdd/2026-10-31-ambre-v0.1-foundation/progress.md` (ignoré par git, peut être absent sur cette machine).

## Avancement : 12 tâches sur 12 réalisées

| # | Tâche | État |
|---|---|---|
| 1–10 | Socle, domaine, CPMM, client Manifold, persistance, synchro, moteurs, portefeuille, pipeline/CLI, API | ✅ (sur `main`) |
| 11 | Dashboard Next.js | ✅ commit `feat(dashboard)` |
| 12 | Docker Compose, docs, validation finale | ✅ commit `docs:` (voir `docs/CHANGELOG.md`) |
| — | Relecture finale de toute la branche | ⬜ à faire |

Validation depuis un clone propre : 260 tests backend, ruff, mypy strict, Vitest (15), lint, typecheck et build du dashboard passent. Détails et chiffres du test réel dans `docs/CHANGELOG.md`.

## Reste à faire

1. **Relecture finale** de la branche (`/code-review` est adapté).
2. **Vérifier visuellement le dashboard** dans un navigateur : l'extension Chrome n'était pas connectée, donc le rendu n'a pas été contrôlé par capture d'écran.
3. **Tester la migration PostgreSQL** (Docker est absent de cette machine).
4. Fusionner, puis supprimer ce fichier.

## Commandes utiles

```bash
uv run pytest -q
uv run ambre init-db && uv run ambre sync --max-pages 2 && uv run ambre cycle
uv run uvicorn ambre.api.app:app --port 8000
cd apps/dashboard && npm run dev
```

Sous Windows, `uv` peut ne pas être dans le PATH : `python -m pip install --user uv`, puis ajouter `%APPDATA%\Python\Python314\Scripts` au PATH.
