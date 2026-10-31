# Tests

## Backend

```bash
uv run pytest -q -W error
uv run ruff check . && uv run ruff format --check . && uv run mypy
```

| Dossier | Contenu |
|---|---|
| `tests/unit` | CPMM, valeur, éligibilité, features, baselines, stratégies, risque, survie, réglages, mapping |
| `tests/property` | Hypothesis : invariants du risque et de la valeur |
| `tests/integration` | Client Manifold, persistance, synchro, portefeuille/exécution, API, CLI |
| `tests/e2e` | Pipeline complet |

## Règles

- Jamais d'Internet ni de vraie mise. `tests/fakes.py` simule le client Manifold.
- Fixtures réelles enregistrées par `scripts/record_fixtures.py` ; fixtures écrites à la main préfixées `synthetic_`.
- Les avertissements sont des erreurs (`-W error`).
- Les tests de chemin sont indépendants de la plateforme (le test du CLI décode l'URL encodée par SQLAlchemy sous Windows).

## Dashboard

```bash
cd apps/dashboard
npm test && npm run lint && npm run typecheck && npm run build
```

Vitest couvre les formateurs (`lib/format.ts`). Les pages n'ont pas de tests automatisés : elles ont été vérifiées par build, types et lint, et leurs endpoints par appels réels à l'API.

## Smoke test réel

`ambre init-db` → `ambre sync --max-pages 2` → `ambre cycle` → API + dashboard. Voir `CHANGELOG.md` pour les chiffres.
