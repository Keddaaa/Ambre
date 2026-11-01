# Architecture

Monolithe modulaire Python (`src/ambre/`) avec des frontières strictes (ADR-001). Deux points d'entrée partagent le même paquet : le worker CLI `ambre` et l'API FastAPI. Le dashboard Next.js (`apps/dashboard`) ne consomme que l'API.

## Flux d'une décision

```text
Manifold GET /v0/markets (lecture seule)
  → data_engine/sync.py            synchro incrémentale (watermark + curseur)
  → infrastructure/                marchés + snapshots horodatés
  → value_engine/eligibility.py    binaire cpmm-1, liquidité, parieurs, clôture
  → feature_engine/features.py     variables disponibles à observed_at (anti-fuite)
  → prediction_engine/baselines.py probabilité estimée ± incertitude
  → strategy_engine/strategies.py  VALUE_HUNTER : Kelly fractionné sur proba pénalisée
  → risk_engine/risk.py            plafonds, impact CPMM, edge minimal
  → survival_engine/survival.py    état opérationnel
  → execution_engine + portfolio   exécution papier atomique, ledger, positions
  → journal des décisions → api/ → apps/dashboard
```

## Règles de dépendance

- Modules **purs** (`domain`, `feature_engine`, `prediction_engine`, `value_engine`, `strategy_engine`, `risk_engine`, `survival_engine`) : ni réseau, ni base, n'importent ni `infrastructure` ni `integrations`.
- Le réseau est confiné à `integrations/manifold`. La persistance est confinée à `infrastructure` (requêtes nommées, aucune logique métier).
- `pipeline.run_cycle` est le seul endroit qui assemble les modules ; il est idempotent (clé `simulation + marché + snapshot + stratégie`).

## Points d'entrée

- CLI : `init-db`, `sync`, `cycle`, `worker`, `new-simulation`. Chaque commande imprime d'abord `mode=… orders_enabled=… db=<url masquée>`.
- API : lecture seule, plus un endpoint d'écriture local, l'arrêt d'urgence (`/control/halt`, `/control/resume`). CORS limité à `CORS_ORIGINS`.
- Dashboard : quatre pages (Overview, Marchés, Journal, Réglages), rafraîchies toutes les 30 s.

## Limites

Le système n'a ni WebSocket, ni backtesting, ni imitation, ni IA contextuelle, ni apprentissage (voir `ROADMAP.md`).
