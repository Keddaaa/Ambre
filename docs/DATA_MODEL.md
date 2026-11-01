# Modèle de données

Source : `src/ambre/infrastructure/orm.py`, migration `migrations/versions/0001_initial.py`. Types portables seulement (SQLite et PostgreSQL, ADR-002).

| Table | Contenu |
|---|---|
| `markets` | Marchés binaires `cpmm-1` retenus (question, clôture, résolution, créateur…) |
| `ignored_markets` | Marchés non traités, avec la **raison** (non binaire, autre mécanisme, token CASH…) |
| `market_snapshots` | Observation datée d'un marché : probabilité, pools, liquidité, volume, parieurs |
| `sync_state` | Watermark et curseur de rattrapage, dernière synchro réussie, dernière erreur |
| `simulations` | Portefeuille virtuel : nom, capital initial, statut `ACTIVE` ou `DEAD` |
| `decisions` | Une ligne par décision journalisée, y compris les refus et les NO_TRADE |
| `positions` | Positions ouvertes et réglées |
| `ledger` | Écritures comptables du portefeuille (cash ↔ coût des positions) |
| `portfolio_snapshots` | Valeur totale, cash, valeur latente, drawdown, état de survie à chaque cycle |
| `control_state` | Arrêt d'urgence (halted, motif, date) |

## Invariants

- Chaque snapshot porte `observed_at`, `event_at` (= `lastUpdatedTime` Manifold, `NULL` si absent), `ingested_at` et `source_id`. Aucune date n'est inventée.
- Une donnée absente est `NULL`, jamais 0 (ex. `conservative_probability`, `ev_per_mana`).
- Les dates sont en UTC. L'API les sérialise sans offset ; le dashboard les lit comme UTC.
- Comptabilité : `cash + coût des positions ouvertes = capital initial + gains réglés`. Vérifié sur le test réel (1 000 exactement avant règlement).
- Idempotence : la clé de décision empêche tout doublon si un cycle est relancé sur les mêmes données.
- Une simulation `DEAD` n'est jamais réinitialisée.
