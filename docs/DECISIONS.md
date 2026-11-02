# Décisions d'architecture (ADR)

## ADR-001 — Monolithe modulaire et écart d'arborescence

- **Problème :** la spec décrit de nombreux moteurs ; il faut une base livrable et testable.
- **Options :** microservices ; monolithe en un seul module ; monolithe modulaire avec frontières strictes.
- **Choix :** monolithe modulaire `src/ambre/`, un dossier par moteur, modules de calcul purs. L'arborescence diffère de celle de la spec (pas de dossiers pour les moteurs futurs).
- **Raisons :** testabilité des calculs sans E/S, déploiement simple, frontières vérifiables.
- **Compromis :** pas de scalabilité indépendante des moteurs.
- **Conséquences :** extraire un moteur plus tard reste possible grâce aux frontières ; les moteurs absents ne sont pas simulés par des fichiers vides.

## ADR-002 — SQLite par défaut, PostgreSQL via Compose, types portables

- **Problème :** démarrer sans infrastructure tout en préparant une vraie base.
- **Options :** PostgreSQL obligatoire ; SQLite seul ; SQLite par défaut avec PostgreSQL optionnel.
- **Choix :** SQLite par défaut (`var/ambre.db`), PostgreSQL 16 via `docker-compose.yml`, uniquement des types SQLAlchemy portables.
- **Raisons :** aucun prérequis pour tester ; migration identique sur les deux bases en principe.
- **Compromis :** la suite de tests tourne sur SQLite ; PostgreSQL est vérifié par migration et cycle réel (voir `docs/CHANGELOG.md`, 2026-11-01).
- **Conséquences :** ne pas supposer de couverture PostgreSQL dans la CI sans l'y ajouter.

## ADR-003 — Remplissage CPMM sur le pool seul, frais à 0

- **Problème :** estimer l'impact d'une mise sur le prix.
- **Options :** simuler le carnet d'ordres limites ; modèle cpmm-1 sur le pool seul.
- **Choix :** formules cpmm-1 de Manifold sur le pool, sans ordres limites, avec `fee_rate = 0.0` (les frais renvoyés par l'API valent 0 sur l'échantillon du 2026-10-31).
- **Raisons :** le carnet n'est pas dans `GET /v0/markets` ; rester à l'API autorisée.
- **Compromis :** le remplissage réel peut être meilleur ou différent si des ordres limites existent.
- **Conséquences :** l'impact est une approximation ; si les frais deviennent non nuls, régler `risk.fee_rate`.

## ADR-004 — Baseline de rétrécissement comme estimateur non validé

- **Problème :** il faut une estimation de probabilité qui ne soit pas un simple écho du prix.
- **Options :** prix du marché seul (aucun edge possible) ; rétrécissement vers le taux de base ; modèle appris.
- **Choix :** `BaseRateShrinkage` (poids marché 0,9). `MarketPriceBaseline` reste comme référence.
- **Raisons :** simple, traçable, sans fuite ; permet de tester toute la chaîne de décision.
- **Compromis :** aucune validation hors échantillon ; aucun avantage sur le marché n'est démontré.
- **Conséquences :** les opportunités sont **théoriques** et ne prouvent aucune rentabilité. La validation (Brier, log loss, backtesting) est prévue en 0.2.

## ADR-005 — `MANIFOLD_MANA` non implémenté en 0.1

- **Problème :** la spec prévoit un mode d'ordres réels en Mana.
- **Options :** l'implémenter derrière un drapeau ; le refuser explicitement.
- **Choix :** le mode lève `ExecutionModeNotAvailable`. Aucun appel d'écriture n'existe dans le code.
- **Raisons :** aucune baseline validée ne justifie des ordres, même en Mana ; moindre surface d'erreur.
- **Compromis :** pas de test de bout en bout avec de vrais ordres.
- **Conséquences :** le réactiver demandera une activation explicite, des garde-fous testés et une nouvelle décision.

## ADR-006 — Navigation autonome en lecture seule, bornée par une liste blanche

- **Problème :** sans information externe, l'estimation ne peut pas dépasser le prix du marché (voir ADR-004) ; Ambre doit pouvoir s'informer et réagir.
- **Options :** aucune navigation ; navigation totalement libre ; navigation libre dans un périmètre de sources autorisées.
- **Choix :** liberté de choix (quoi chercher, quand réagir) à l'intérieur d'une liste blanche de réseaux et de sources fournie par le propriétaire. Lecture seule, contenu web traité comme donnée non fiable, consultations journalisées, exécution isolée.
- **Raisons :** l'information est nécessaire à l'objectif ; la liste blanche limite la surface d'attaque (injection de prompt, fuite) et garde le comportement vérifiable.
- **Compromis :** une source utile hors liste n'est pas consultée tant qu'elle n'est pas ajoutée ; l'isolation ajoute de la mise en place.
- **Conséquences :** module non implémenté en 0.1 (voir `ROADMAP.md`). Liste blanche encore à définir. Aucune des interdictions de `AGENTS.md` n'est levée. L'apport réel des sources reste à mesurer hors échantillon.

## ADR-007 — Évaluation hors échantillon sans fuite

- **Problème :** mesurer le modèle contre le prix du marché sans surévaluer sa qualité.
- **Options :** scorer tous les marchés résolus ; scorer la dernière décision de chaque marché ; scorer la première décision, seulement si elle précède la résolution.
- **Choix :** une seule observation par marché (sa première décision), uniquement pour les résolutions `YES`/`NO` dont `resolution_at` est connu et postérieur à la décision.
- **Raisons :** les décisions tardives se rapprochent du prix final et gonflent le score ; une résolution sans date ne permet pas d'exclure une fuite ; une observation par marché évite de compter plusieurs fois la même issue.
- **Compromis :** échantillon petit au début ; l'intervalle est une approximation normale, peu fiable sous ~30 marchés (`sufficient_sample` reste faux en dessous).
- **Conséquences :** aucune conclusion d'avantage tant que l'échantillon est insuffisant. Le backtesting complet reste à faire.

## ADR-008 — Worker de synchronisation dans un conteneur Docker

- **Contexte :** la synchronisation automatique existait (`ambre worker`) mais s'arrêtait avec la session WSL ou le terminal.
- **Décision :** un service `worker` dans `docker-compose.yml`, construit depuis un `Dockerfile` (Python 3.12, `uv sync --frozen --no-dev`). Il applique les migrations (`ambre init-db`) puis lance `ambre worker --interval 300`, avec `restart: unless-stopped`. `EXECUTION_MODE=PAPER` et `ENABLE_MANIFOLD_ORDERS=false` sont fixés dans le fichier compose, pas lus dans `.env`.
- **Raisons :** redémarrage automatique avec Docker, sans service système à écrire ; le mode papier ne peut pas être changé par une variable d'environnement oubliée.
- **Compromis :** le service exige PostgreSQL (la base SQLite par défaut reste pour le développement local) ; `POSTGRES_PASSWORD` doit tenir dans une URL sans encodage (hexadécimal recommandé, voir `.env.example`). L'API et le dashboard ne sont pas conteneurisés.
- **Conséquences :** `docker compose up -d` lance Postgres et la synchronisation. Toujours des GET Manifold uniquement ; aucun secret dans l'image (`.env` exclu par `.dockerignore`).

## ADR-009 — Actions manuelles papier, tracées par acteur

- **Contexte :** le dashboard ne pouvait qu'arrêter et reprendre. Le propriétaire veut agir depuis le dashboard, et pouvoir faire agir Claude par la même voie, sans que l'évaluation de l'algorithme soit faussée.
- **Décision :** chaque décision porte un `actor` (`AMBRE` par défaut, `USER`, `CLAUDE`). `POST /orders` crée une mise papier manuelle (stratégie `MANUAL`) : l'acteur fournit sa propre P(YES) et une mise ; la probabilité est pénalisée par l'incertitude de la baseline, puis le **même risk engine** plafonne ou refuse. Un refus est journalisé. `request_id` rend l'envoi idempotent. `POST /control/run-cycle` lance un cycle sur les données stockées. L'acteur `AMBRE` ne peut pas être fourni par l'API.
- **Raisons :** une action manuelle ne doit jamais contourner les garde-fous ; la traçabilité par acteur permet d'évaluer Ambre séparément de ses opérateurs. Une réponse de LLM n'est pas une probabilité (ADR-004) : la probabilité de Claude est une déclaration d'acteur, journalisée comme telle, jamais une entrée du modèle.
- **Compromis :** l'API n'a pas d'authentification (voir `ROADMAP.md`) : le champ `actor` est déclaratif. La clôture manuelle d'une position n'existe pas : le CPMM de vente n'est pas encore implémenté et une clôture au prix affiché inventerait une exécution.
- **Conséquences :** migration `0002` (colonne `decisions.actor`, `AMBRE` pour l'existant). `evaluate` compte aujourd'hui la première décision de chaque marché quel que soit l'acteur ; filtrer par acteur reste à faire avant d'interpréter un score.
