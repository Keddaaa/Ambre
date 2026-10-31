# AMBRE — Contexte du projet

> Synthèse courte de l'état réel du code au commit `6be2aa5` (branche `feat/ambre-v0.1`).
> Pour le détail : la spec (`docs/superpowers/specs/2026-10-31-ambre-master-spec.md`) et le plan (`docs/superpowers/plans/2026-10-31-ambre-v0.1-foundation.md`).
> Pour l'avancement : `REPRISE.md`.

## Ce qu'est AMBRE

**AMBRE** (*Adaptive Market Betting & Reasoning Engine*) est une plateforme expérimentale de prédiction et de **paris virtuels** sur [Manifold Markets](https://manifold.markets). Elle observe les marchés, estime des probabilités, repère les écarts avec le prix du marché et décide s'il faut miser. Une décision ne devient une mise simulée qu'après un moteur de risque et un moteur de survie indépendants. Toutes les décisions sont journalisées.

- **Mode par défaut :** `PAPER`, avec un portefeuille virtuel local de **1 000 Mana** et aucune écriture sur Manifold. Le mode `MANIFOLD_MANA` (vrais ordres en Mana) est refusé en v0.1.
- **Monnaie :** aucune transaction en argent réel. Le Mana n'est pas de l'argent, et aucun résultat ne prouve une rentabilité.
- **Objectif de la v0.1 :** une fondation qui marche vraiment, de bout en bout. Les moteurs avancés viendront ensuite : imitation, IA contextuelle, apprentissage, backtesting.

## Flux d'une décision

```text
Manifold API (GET /v0/markets, lecture seule)
  → sync incrémentale (watermark + curseur de rattrapage)      data_engine/sync.py
  → stockage marchés + snapshots horodatés                     infrastructure/
  → éligibilité (binaire cpmm-1, liquidité, parieurs, clôture) value_engine/eligibility.py
  → features (disponibles à observed_at, anti-fuite)           feature_engine/features.py
  → estimation de probabilité (baseline)                       prediction_engine/baselines.py
  → stratégie VALUE_HUNTER (Kelly fractionné, pénalisé)        strategy_engine/strategies.py
  → moteur de risque (plafonds, impact CPMM, edge minimal)     risk_engine/risk.py
  → moteur de survie (état opérationnel)                       survival_engine/survival.py
  → exécution papier + ledger + positions (idempotent)         execution_engine/, portfolio/
  → journal des décisions → API FastAPI → dashboard            api/, apps/dashboard (en cours)
```

## Modules en place (`src/ambre/`)

| Module | Rôle |
|---|---|
| `config/settings.py` | Réglages (`.env` + `configs/default.yaml`), valeurs par défaut sûres |
| `domain/models.py` | Contrats Pydantic partagés et enums |
| `integrations/manifold/` | Client HTTP en lecture seule (retry, backoff, jitter) et conversion JSON → modèles |
| `infrastructure/` | SQLAlchemy, Alembic, requêtes (aucune logique métier) |
| `data_engine/sync.py` | Synchro paginée, reprise après panne, déduplication |
| `feature_engine/` | Variables explicatives datées |
| `prediction_engine/` | `MarketPriceBaseline`, `BaseRateShrinkage` |
| `value_engine/` | Mathématiques CPMM (cpmm-1 de Manifold), EV, Kelly, éligibilité |
| `strategy_engine/` | `ValueHunter`, `NoTrade` |
| `risk_engine/` | Plafonds de mise et d'exposition, frais inclus, rejet des valeurs non finies |
| `survival_engine/` | États BALANCED, DEFENSIVE, OBSERVING, CRITICAL et HALTED |
| `portfolio/` | Ledger, positions, valorisation latente, règlement des résolutions |
| `execution_engine/` | Garde de mode d'exécution, exécuteur papier atomique |
| `pipeline.py` | `run_cycle` : un cycle complet de décision |
| `cli.py` | Commande `ambre` : `init-db`, `sync`, `cycle`, `worker`, `new-simulation` |
| `api/` | API FastAPI en lecture, plus l'arrêt d'urgence (halt/resume) |

Les modules de calcul (valeur, risque, survie, prédiction, features, stratégie) sont **purs**. Ils ne font ni réseau ni accès base.

## Règles importantes

**Paramètres de risque initiaux.** Ils sont expérimentaux et non optimisés :

| Paramètre | Valeur |
|---|---|
| Mise maximale | 2 % de la valeur du portefeuille |
| Exposition totale maximale | 20 % de la valeur du portefeuille |
| Fraction de Kelly | 0,25 |
| Edge minimal après impact | 3 % |

**Survie.** Les règles s'appliquent dans cet ordre, la première qui correspond l'emporte :
1. Arrêt manuel → HALTED.
2. Exposition inconnue → OBSERVING.
3. Valorisation invalide → HALTED.
4. Valeur ≤ 50 % du capital initial → HALTED, et la simulation est déclarée morte.
5. Données de plus de 15 min → OBSERVING.
6. Drawdown ≥ 30 % → CRITICAL.
7. Drawdown ≥ 15 % → DEFENSIVE (mises × 0,5).
8. Sinon → BALANCED.

**Autres règles.**
- Une donnée absente reste `null`, jamais 0. Une simulation morte n'est jamais réinitialisée : une nouvelle simulation est créée et l'ancienne est archivée.
- Les tests n'utilisent jamais le réseau. Les fixtures Manifold sont réelles ; celles écrites à la main sont préfixées `synthetic_`.

## Stack

- **Backend :** Python 3.12 (géré par uv), FastAPI, Pydantic v2, HTTPX, SQLAlchemy 2, Alembic.
- **Base de données :** SQLite par défaut, PostgreSQL prévu via Docker Compose.
- **Tests :** pytest, Hypothesis ; lint et types avec ruff et mypy strict.
- **Frontend (en cours) :** Next.js, TypeScript strict, Tailwind, shadcn/ui, TanStack Query, Recharts.

## Démarrer

```bash
uv sync
uv run ambre init-db
uv run ambre sync --max-pages 2
uv run ambre cycle            # ou: uv run ambre worker --interval 300
uv run uvicorn ambre.api.app:app --port 8000
uv run pytest -q              # 260 tests
```

## Limites connues (v0.1)

- **Baseline non validée :** les opportunités détectées sont *théoriques*.
- **Simulation CPMM partielle :** elle ignore les ordres limites du carnet.
- **PostgreSQL non testé** sur cette machine, car Docker est absent.
- **Synchro par polling :** pas de WebSocket.
- **Pas encore faits :** backtesting, imitation, IA contextuelle, apprentissage continu.
