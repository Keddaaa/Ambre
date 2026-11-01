# Changelog

## 0.1.0 — 2026-10-31

### Ajouté

- Synchronisation incrémentale en lecture seule des marchés binaires Manifold (watermark, curseur de rattrapage, reprise après panne).
- Stockage des marchés et des snapshots horodatés (`observed_at`, `event_at`, `ingested_at`, `source_id`).
- Éligibilité, features sans fuite, baselines de probabilité, stratégie `VALUE_HUNTER`, moteur de risque, moteur de survie.
- Portefeuille virtuel de 1 000 Mana, exécution papier atomique et idempotente, ledger, règlement des résolutions (YES/NO/MKT/CANCEL).
- CLI `ambre` : `init-db`, `sync`, `cycle`, `worker`, `new-simulation`.
- API FastAPI en lecture, plus l'arrêt d'urgence (`halt`/`resume`).
- Dashboard Next.js : Overview, Marchés (liste et détail), Journal, Réglages.
- `docker-compose.yml` (PostgreSQL 16 optionnel) et documentation.

### Validation (clone propre de la branche, Windows 11, Python 3.12 via uv, Node)

| Commande | Résultat réel |
|---|---|
| `uv run ruff check .` | All checks passed |
| `uv run ruff format --check .` | 68 files already formatted |
| `uv run mypy` | Success, 38 source files |
| `uv run pytest -q -W error` | **260 passed** en 14,11 s |
| `npm test` (Vitest) | 15 passed |
| `npm run lint` | aucun problème |
| `npm run typecheck` | aucune erreur (voir ci-dessous) |
| `npm run build` | compilé, 8 pages générées |

Vérifications de sécurité : aucun secret en dur (`git grep`), aucun `v0/bet` dans `src/`.

### Smoke test réel (lecture seule sur api.manifold.markets, base neuve)

- `ambre sync --max-pages 2` : 1 069 marchés stockés, 931 ignorés (non binaires), 0 invalide.
- `ambre cycle` : état BALANCED ; 51 marchés évalués, 43 inéligibles, 34 mises simulées, 10 refusées, 7 sans pari, 106 sautés faute de capacité.
- Comptes : cash + coût des positions = 1 000,00 exactement ; valeur totale latente 978,61 (spread et impact à l'achat).
- API : `/portfolio`, `/markets`, `/markets/{id}`, `/decisions`, `/system` répondent avec des données réelles ; `halt` puis `resume` fonctionnent.
- Dashboard : les quatre routes répondent HTTP 200.

### Corrigé pendant la validation

- Le test du CLI échouait sous Windows (SQLAlchemy encode le chemin de la base dans l'URL masquée). Il compare désormais l'URL décodée.
- `tsc --noEmit` échouait sur un clone propre, car `PageProps`/`LayoutProps` sont générés par Next. Le script `npm run typecheck` lance `next typegen` d'abord.
- La page de détail et la navigation sont enveloppées dans `<Suspense>` (exigé par Cache Components).

### Problèmes connus

- **Baseline non validée :** les opportunités sont théoriques ; aucune mesure hors échantillon (ADR-004).
- **PostgreSQL vérifié partiellement (2026-11-01, WSL2 + Docker Desktop, PostgreSQL 16) :** `alembic upgrade head`, `alembic check` (aucun écart), `downgrade base` puis `upgrade head`, et un cycle `sync` + `cycle` réel (1022 marchés, 103 décisions, 42 positions papier) passent. La suite de tests reste exécutée sur SQLite uniquement.
- **Simulation CPMM partielle :** elle ignore les ordres limites du carnet (ADR-003).
- **Synchro par polling :** pas de WebSocket.
- **Pas de backtesting**, ni imitation, IA contextuelle, apprentissage.
- **Rendu du dashboard non contrôlé visuellement :** l'extension navigateur n'était pas connectée ; les pages ont été vérifiées par build, types, lint, HTTP 200 et appels API, pas par capture d'écran.
- **Pas d'authentification** sur l'API ; avis `npm audit` non examinés.
