# AMBRE — Adaptive Market Betting & Reasoning Engine

Plateforme expérimentale de **paris virtuels** sur [Manifold Markets](https://manifold.markets). Elle lit les marchés binaires, estime une probabilité, calcule l'écart avec le prix après impact CPMM, puis fait passer chaque proposition par un moteur de risque et un moteur de survie avant de simuler la mise. Toutes les décisions sont journalisées.

> **Mana ≠ argent réel. Aucun résultat n'indique une rentabilité.**
> Le mode par défaut est `PAPER` (portefeuille local de 1 000 Mana). Aucune écriture n'est faite sur Manifold, et le mode `MANIFOLD_MANA` est refusé en 0.1.

## Prérequis

- [uv](https://docs.astral.sh/uv/) (installe Python 3.12 au besoin)
- Node ≥ 20
- Docker, optionnel (PostgreSQL uniquement)

## Installation

```bash
uv sync
cp .env.example .env              # optionnel : les valeurs par défaut sont sûres
uv run ambre init-db              # crée var/ambre.db et applique les migrations
```

## Lancer

```bash
uv run ambre sync --max-pages 2   # lecture seule de Manifold
uv run ambre cycle                # un cycle de décision papier
uv run ambre worker --interval 300   # sync + cycle en boucle
uv run uvicorn ambre.api.app:app --reload --port 8000
cd apps/dashboard && npm install && npm run dev   # http://localhost:3000
```

`uv run ambre new-simulation --name <nom>` crée une nouvelle simulation. Une simulation morte n'est jamais réinitialisée.

## PostgreSQL (optionnel)

```bash
# définir POSTGRES_PASSWORD dans .env, puis :
docker compose up -d
DATABASE_URL=postgresql+psycopg://ambre:<mot de passe>@localhost:5432/ambre uv run ambre init-db
```

La migration et un cycle réel ont été vérifiés sur PostgreSQL 16 ; les tests tournent sur SQLite (voir `docs/CHANGELOG.md`).

## Tests

```bash
uv run pytest -q
uv run ruff check . && uv run ruff format --check . && uv run mypy
cd apps/dashboard && npm test && npm run lint && npm run typecheck && npm run build
```

Les tests n'utilisent jamais Internet.

## Documentation

`AGENTS.md` (règles pour contribuer) · `docs/ARCHITECTURE.md` · `docs/DATA_MODEL.md` · `docs/STRATEGIES.md` · `docs/PREDICTION_ENGINE.md` · `docs/SURVIVAL_ENGINE.md` · `docs/API_INTEGRATION.md` · `docs/SECURITY.md` · `docs/TESTING.md` · `docs/DECISIONS.md` · `docs/ROADMAP.md` · `docs/CHANGELOG.md`
