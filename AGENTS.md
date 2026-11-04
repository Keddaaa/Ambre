# AGENTS.md — AMBRE

## Objectif

Plateforme de paris **virtuels** sur Manifold (Mana, mode `PAPER`). Fondation 0.1 : sync → features → estimation → stratégie → risque → survie → exécution papier → journal → API → dashboard.

## Sources de vérité

- Spec : `docs/superpowers/specs/2026-10-31-ambre-master-spec.md`
- Plan 0.1 : `docs/superpowers/plans/2026-10-31-ambre-v0.1-foundation.md`
- Décisions : `docs/DECISIONS.md`

Les autres documents de `docs/` décrivent l'état du code. Ils ne dupliquent pas ce fichier.

## Carte des modules (`src/ambre/`)

| Module | Rôle | Pur ? |
|---|---|---|
| `config` | Réglages (`.env` + `configs/default.yaml`) | — |
| `domain` | Contrats Pydantic et enums | oui |
| `integrations/manifold` | Client HTTP en lecture seule, mapping JSON → modèles | non (réseau) |
| `infrastructure` | SQLAlchemy, dépôts, sessions | non (base) |
| `data_engine` | Synchronisation incrémentale | non |
| `feature_engine`, `prediction_engine`, `value_engine`, `strategy_engine`, `risk_engine`, `survival_engine`, `evaluation_engine` | Calculs | **oui** |
| `portfolio`, `execution_engine` | Ledger, positions, exécuteur papier | non |
| `intelligence` | Réseau d'acquisition d'information (ADR-010) : `models`, `budget`, `registry`, `normalization`, `planning`, `value`, `routing`, `matching`, `resolution`, `domain_params`, `generic_planning`, `context`, `estimation`, `queries`, `states`, `feature_journal`, `news_parsing`, `news_events` sont **purs** ; `providers/`, `store`, `orm`, `service`, `connectors`, `revalidation` ne le sont pas. Lecture seule, ne décide jamais d'une mise | mixte |
| `cognitive` | Cognitive Vault (ADR-012) : notes, relations, règles et contraintes. `models`, `constraints`, `lifecycle`, `markdown` sont **purs** ; `orm`, `vault`, `rules` ne le sont pas. Les règles sont des données inertes tant qu'elles ne sont pas ACTIVE ; aucun accès au chemin de pari | mixte |
| `pipeline.py`, `cli.py`, `api/` | Orchestration, CLI, FastAPI | non |

**Règle de dépendance :** les modules purs n'importent ni `infrastructure` ni `integrations`. Les moteurs quantitatifs ne dépendent ni de `intelligence` ni de `cognitive`, et `intelligence` comme `cognitive` n'importent ni `execution_engine`, ni `portfolio`, ni `risk_engine` (testé). Le réseau reste dans `integrations/manifold`, la persistance dans `infrastructure`.

## Commandes

```bash
uv run pytest -q
uv run ruff check . && uv run ruff format --check .
uv run mypy
cd apps/dashboard && npm test && npm run lint && npm run typecheck && npm run build
```

## Interdictions

- Aucun module de trading en monnaie réelle.
- Aucun appel à `POST /v0/bet` ni à un autre endpoint d'écriture Manifold. `MANIFOLD_MANA` lève `ExecutionModeNotAvailable`. Une clé API ne débloque rien.
- Marchés traités : `BINARY` + `cpmm-1` uniquement ; `token == "CASH"` exclu.
- Une donnée absente reste `None`/`null`, jamais 0. Aucune date inventée sur un snapshot.
- Kelly intégral interdit : toujours sur probabilité pénalisée, fractionné, puis plafonné par le risque.
- Tests sans Internet ni vraie mise. Fixtures écrites à la main préfixées `synthetic_`.
- Aucun secret dans Git. Ne pas créer de modules vides pour les moteurs futurs (voir `docs/ROADMAP.md`).
- Code et identifiants en anglais, docs en français.

## Procédure de modification

1. Écrire d'abord le test qui échoue.
2. Implémenter, puis lancer les commandes ci-dessus. Tout doit passer.
3. Si le choix est structurant, ajouter un ADR dans `docs/DECISIONS.md`.
4. Ne jamais affirmer qu'une fonctionnalité marche sans l'avoir vérifiée.

## Critères d'acceptation (spec, Partie XXII)

Implémentée, contrats respectés, tests essentiels verts, erreurs gérées, métriques observables, documentée, intégrée au pipeline, limites explicites. Pour la prédiction : baseline, protocole reproductible, résultats hors échantillon mesurés, performances jamais inventées. Pour la transaction : mode simulation fonctionnel, garde-fous testés, aucune transaction distante sans activation explicite.
