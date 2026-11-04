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

- L'API n'authentifie personne. Elle se protège d'une page web hostile (CSRF, DNS rebinding), pas d'un processus local (ADR-020).
- Routes d'écriture : `POST /control/halt`, `/control/resume`, `/control/run-cycle`, `/orders`. Aucune n'appelle Manifold ni ne change le mode d'exécution.
- Toute écriture exige `application/json`, et une `Origin` présente doit figurer dans `CORS_ORIGINS` (par défaut `http://localhost:3000`). Toute requête doit porter un `Host` de `API_ALLOWED_HOSTS`.
- À n'écouter que sur `127.0.0.1`. Le dashboard aussi : `next dev` et `next start` écoutent sur toutes les interfaces sans `-H 127.0.0.1`.

## Secret exposé : `logswsl.txt`

- Le fichier contient un identifiant et un mot de passe. Le service n'est pas établi : le nom évoque le compte WSL de l'autre PC, aucune configuration du dépôt ne réutilise la valeur, et ce compte n'existe pas sur le Mac.
- Il a été versionné par `8423ad1` et poussé sur `origin/main` et `origin/feat/backtest`. Le dépôt est privé, sans fork ni autre collaborateur.
- Il n'est plus suivi, mais reste dans l'historique et dans le dossier local.
- La valeur figure aussi dans une transcription Claude Code locale du 2026-11-04 (`cat logswsl.txt`) et a donc été envoyée au modèle.

### À faire par le propriétaire

1. Identifier le service, puis changer ce mot de passe là-bas. C'est la seule action qui neutralise le secret.
2. Supprimer le fichier local et la transcription concernée.
3. Une fois le mot de passe changé, réécrire l'historique si souhaité, sur toutes les branches, puis pousser en force. Les autres clones devront être refaits :

```bash
git filter-repo --invert-paths --path logswsl.txt
git push --force --all origin
```

## Vérifications

```bash
git grep -nE "(api[_-]?key|secret|password)\s*=\s*['\"][^'\"]+" -- ':!*.example' ':!docs/*'   # aucun résultat
git grep -n "v0/bet" -- src/                                                                  # aucun résultat
```

## Redémarrage supervisé (macOS)

Les modèles de `deploy/launchd/` ne sont pas chargés. Ils lancent les binaires directement, sans `uv run`, écoutent sur `127.0.0.1` et redémarrent en cas d'arrêt.

```bash
ROOT=$PWD; mkdir -p var/logs
(cd apps/dashboard && npm run build)
for s in worker api dashboard; do
  sed "s#__ROOT__#$ROOT#g" deploy/launchd/com.ambre.$s.plist > ~/Library/LaunchAgents/com.ambre.$s.plist
  launchctl load ~/Library/LaunchAgents/com.ambre.$s.plist
done
uv run ambre worker-status
```

Un seul worker par base : le bail l'impose, et il n'y a qu'une simulation active par base. Ne pas lancer de worker Docker sur la même base. Après un arrêt brutal, le nouveau worker attend l'expiration du bail (20 minutes au plus à l'intervalle de 300 s).

Le test en avant d'ADR-019 reste une instance séparée (`docker-compose.forward.yml`).

## Limites connues

Pas d'authentification sur l'API : tout processus local peut écrire ; `npm ci` signale des avis `npm audit` sur le dashboard, non examinés ni traités en 0.1.
