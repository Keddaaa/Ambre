# Sécurité

## Garde-fous d'exécution

- `EXECUTION_MODE=PAPER` et `ENABLE_MANIFOLD_ORDERS=false` par défaut.
- `MANIFOLD_MANA` lève `ExecutionModeNotAvailable` en 0.1. La présence d'une clé API n'autorise rien.
- Aucun code ne fait de requête d'écriture vers Manifold.
- Aucune monnaie réelle : le Mana n'est pas de l'argent.

## Secrets

- `.env` est ignoré par Git ; `.env.example` ne contient aucune valeur secrète.
- Le CLI masque le mot de passe de l'URL de base et ne lit jamais la clé API.
- `docker-compose.yml` exige `POSTGRES_PASSWORD` depuis `.env` et n'expose Postgres que sur `127.0.0.1`.

## API

- Lecture seule, sauf `halt`/`resume`. CORS restreint à `CORS_ORIGINS` (par défaut `http://localhost:3000`).
- À ne pas exposer sur un réseau public : l'API n'a pas d'authentification en 0.1.

## Vérifications

```bash
git grep -nE "(api[_-]?key|secret|password)\s*=\s*['\"][^'\"]+" -- ':!*.example' ':!docs/*'   # aucun résultat
git grep -n "v0/bet" -- src/                                                                  # aucun résultat
```

## Limites connues

Pas d'authentification sur l'API ; `npm ci` signale des avis `npm audit` sur le dashboard, non examinés ni traités en 0.1.
