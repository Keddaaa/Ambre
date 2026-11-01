# Intégration API

## Manifold (entrant, lecture seule)

Code : `src/ambre/integrations/manifold/`. Endpoints autorisés, vérifiés le 2026-10-31 :

- `GET /v0/markets` — `limit` ≤ 1000, `sort=updated-time`, `order=desc`, `before=<marketId>` (curseur)
- `GET /v0/market/{id}`

Limite : 500 requêtes/min/IP. Retry avec backoff exponentiel et jitter sur 429/5xx. Aucun contournement, aucun scraping. Aucun endpoint d'écriture n'est appelé (`git grep "v0/bet" -- src/` est vide).

Un JSON invalide ou un champ requis manquant sur un marché isolé rejette ce marché avec une raison, sans faire échouer la page.

## API AMBRE (sortante)

FastAPI, `src/ambre/api/app.py`. Schémas dans `api/schemas.py`, miroir TypeScript dans `apps/dashboard/lib/types.ts`.

| Méthode | Route | Rôle |
|---|---|---|
| GET | `/health` | Santé et base |
| GET | `/system` | Mode, ordres, arrêt, synchro, stratégie active, limites de risque et de survie |
| GET | `/evaluation` | Brier, log loss et calibration du modèle et du prix (voir `EVALUATION.md`) |
| GET | `/markets` | Liste filtrable (`status`, `q`, `limit`, `offset`) |
| GET | `/markets/{id}` | Marché, snapshots, décisions |
| GET | `/decisions` | Journal (`action`, `limit`, `offset`) |
| GET | `/portfolio` | Valeur, cash, positions, drawdown, survie (404 sans simulation) |
| GET | `/portfolio/history` | Historique de valeur |
| POST | `/control/halt` | Arrêt d'urgence (`{"reason": …}`) |
| POST | `/control/resume` | Reprise (`{"confirm": true}`) |

Les écritures sont protégées contre le CSRF et limitées aux origines configurées.
