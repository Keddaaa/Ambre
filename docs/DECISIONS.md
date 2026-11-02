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

## ADR-010 — Global Intelligence Network : liste blanche, registre et recherche adaptative

- **Contexte :** ADR-006 prévoit une navigation autonome bornée par une liste blanche que le propriétaire devait fournir. Le propriétaire a fourni un catalogue de fournisseurs (actualités, finance, météo, science, etc.) et demande un système d'acquisition d'information qui décide lui-même quoi chercher.
- **Décision :** (1) la liste blanche d'ADR-006 est le **registre des fournisseurs** (`configs/`), où seuls les fournisseurs dont la fiche est complétée dans `docs/API_CATALOG.md` peuvent être activés ; initialement 8 : GDELT, Open-Meteo, NASA EONET, USGS, Hacker News, GitHub, Bluesky (lecture de comptes publics), Manifold. (2) Le réseau produit des **observations normalisées** et des signaux structurés ; il ne déclenche jamais une mise, le Risk Engine garde le dernier contrôle. (3) La recherche d'information démarre déterministe (table source par type d'information, budgets, règle d'arrêt), puis un classement statistique, et un bandit contextuel seulement après validation du protocole. (4) L'apport des sources n'est affirmé qu'après comparaison chronologique hors échantillon avec et sans la source (ADR-007).
- **Raisons :** l'information externe est la condition pour dépasser le prix du marché (ADR-004). Une liste blanche vérifiée limite la surface d'attaque et le risque contractuel ; une politique déterministe mesurable précède tout apprentissage.
- **Compromis :** 8 fournisseurs seulement au départ ; quotas inconnus traités comme les plus bas ; aucune recherche par mots-clés sur Bluesky sans compte ; Open-Meteo et Manifold limités à un usage non commercial ; GDELT lent (1 requête / 5 s). Les noms du catalogue non vérifiés restent désactivés.
- **Conséquences :** nouveau paquet `ambre.intelligence`, créé sous-module par sous-module avec ses tests (jamais de dossier vide). Les modules purs de ce paquet n'importent ni `infrastructure` ni `integrations`. `AGENTS.md` sera mis à jour à la phase B, quand le premier module existe. Aucune interdiction d'`AGENTS.md` n'est levée. Voir `docs/INTELLIGENCE_ARCHITECTURE.md`.
- **Confirmation :** le propriétaire a confirmé le 2026-11-02 que ces 8 fournisseurs forment la liste blanche initiale.

## ADR-011 — Premier pipeline d'intelligence : USGS, liens candidats, aucune influence sur les paris

- **Contexte :** les marchés ouverts réels contiennent des marchés sur les séismes (deux au 2026-11-02) et aucun sur la météo. ADR-010 laisse l'ordre des connecteurs libre.
- **Décision :** le premier connecteur de bout en bout est USGS (séismes), pas Open-Meteo. Une passe de recherche (`ambre research`) planifie les lacunes depuis la question des marchés, regroupe les besoins identiques en une seule requête, applique le budget, interroge, normalise, stocke, relie les observations aux marchés et journalise chaque décision, y compris celles de ne pas chercher. Les liens sont des **candidats** (`candidate_event`) : `usable_for_prediction` vaut toujours `False`, car les critères de résolution des marchés ne sont pas lus (la description n'est pas stockée).
- **Raisons :** une observation USGS confirmée ne prouve pas qu'un marché est résolu (zone, date, définition de la magnitude). Traiter un rapprochement de texte comme un fait violerait « ne pas considérer une information comme exploitable avant sa validation ». Le moteur de recherche ne doit pas pouvoir miser : `intelligence` n'importe aucun module d'exécution, de portefeuille ou de risque.
- **Compromis :** le planificateur ne comprend que les questions sur les séismes ; les régions viennent d'un gazetteer écrit à la main (`configs/gazetteer.yaml`, deux régions) ; la valeur d'information est un classement a priori (`expected_information_gain` reste `None`) ; la recherche est lancée à la main, sans planificateur périodique. Un échec, un résultat vide non concluant ou une recherche sautée ne comptent jamais comme « marché informé » (`markets_without_information`).
- **Conséquences :** migration `0003` (tables `intel_*`, vérifiée sur PostgreSQL 16 : montée, absence de dérive, descente, remontée). Aucune décision, position ni écriture du ledger n'est produite par ce paquet (testé). Prochaines étapes : lecture des critères de résolution, vérification croisée et regroupement d'événements, planificateur périodique, mesure de la contribution avec et sans source, puis apprentissage.

## ADR-012 — Cognitive Vault : mémoire structurée, règles inertes et contraintes immuables

- **Contexte :** Ambre doit accumuler des connaissances et proposer des règles de fonctionnement sans pouvoir s'auto-modifier de façon incontrôlée ni toucher au chemin de pari.
- **Décision :** (1) le paquet `ambre.cognitive` stocke des **notes** typées (13 types), versionnées et reliées par des **relations** avec provenance, dans les tables `cognitive_*` et `knowledge_edges` (SQLAlchemy est la source de vérité ; le Markdown est un export en lecture). (2) Une **règle** est une donnée inerte (`rule_proposals`, historique append-only `rule_versions`) : elle suit DRAFT → PROPOSED → TESTING → VALIDATED → ACTIVE → DEPRECATED, plus REJECTED et ROLLED_BACK. Elle ne change le comportement que lorsqu'elle est ACTIVE. (3) Les **contraintes de niveau A** sont dans le code : liste fermée d'actions (aucune ne peut miser, toucher au mode, aux secrets ou à l'arrêt d'urgence), noms protégés refusés, une limite ne peut qu'être resserrée (jamais assouplie, mesurée contre le `RiskConfig` réel), et une règle ne peut pas renoncer aux limites dures. (4) Tant que le moteur d'expériences n'existe pas, la preuve d'une validation est fournie par l'appelant et n'est pas vérifiable : **seul le propriétaire (`USER`) peut valider ou activer**. Ambre et Claude proposent, testent, rejettent.
- **Raisons :** une règle créée n'est pas une règle validée ; une preuve que l'agent écrit lui-même ne prouve rien ; les invariants de sécurité doivent survivre à toute suite de propositions (testé par propriété avec Hypothesis).
- **Compromis :** pas encore de raccourcis, d'expériences isolées, de méta-apprentissage ni d'intégration au pipeline : aucune règle ACTIVE n'est consommée par un moteur. La détection de doublons est exacte (empreinte du texte normalisé), pas sémantique. La recherche est lexicale (tous les termes, titre pondéré), sans index vectoriel.
- **Conséquences :** migration `0004` (vérifiée sur PostgreSQL 16). Les tables du vault n'ont aucune clé étrangère vers décisions, positions, ledger ni simulations. Les moteurs quantitatifs n'importent pas `cognitive` et `cognitive` n'importe aucun module d'exécution, de portefeuille, de risque, de survie ni d'intégration réseau (testé). `ambre vault-export --dir` écrit le Markdown.

## ADR-013 — Compréhension des marchés avant la collecte, planificateur par domaine, estimation conditionnée à un modèle validé

- **Contexte :** le planificateur ne lisait que les questions sur les séismes, avec deux régions codées. Les liens événement/marché n'alimentaient aucun moteur, et rien n'empêchait de confondre date de clôture et date de l'événement.
- **Décision :** (1) un **Resolution Intelligence Engine** déterministe (sans modèle de langage) extrait de la question le domaine, les entités, le lieu, la période, les seuils et leur direction, les conditions YES/NO, la source de référence, les ambiguïtés et les manques. (2) Un **planificateur générique** applique le contrat de chacun des neuf domaines ; les règles viennent de `configs/research_rules.yaml` et `configs/gazetteer.yaml`. Un marché est `COLLECTABLE`, `AMBIGUOUS`, `OUT_OF_HORIZON`, `NO_CONNECTOR` ou `NOT_APPLICABLE`, et **collectable ≠ estimable**. (3) Open-Meteo devient le deuxième connecteur (prévisions journalières ≤ 16 jours). (4) Un **Context Feature Bridge** ne transforme en caractéristiques que les informations d'un fournisseur activé et vérifié, valides et **déjà disponibles** à la décision ; il ajoute des valeurs `ctx.` à une copie du vecteur de `feature_engine`. (5) L'**estimation** exige un modèle validé et applicable ; sinon `INSUFFICIENT_MODEL` (jamais de probabilité) ou `NOT_ESTIMABLE`.
- **Raisons :** une règle explicite ne peut pas inventer un critère absent ; l'ambiguïté doit rester visible ; une prévision n'est pas une observation et une actualité n'est pas une probabilité (ADR-004).
- **Compromis :** l'extraction ne comprend que les formulations prévues (le reste est signalé, pas deviné) ; les descriptions de marché, où vivent les critères de résolution, ne sont pas stockées ; aucun modèle n'existe, donc aucune estimation n'est produite ; sept domaines n'ont pas de connecteur.
- **Conséquences :** `InfoNeed.WEATHER_FORECAST`, charge utile `WeatherForecastPayload`, `ambre research` couvre séismes et météo, regroupe les marchés d'un même lieu en une requête et journalise la compréhension de chaque marché. Aucune règle de pari n'est ajoutée : le Risk Engine garde le dernier contrôle.

## ADR-014 — Planificateur de recherche persistant, branché dans le worker

- **Contexte :** `ambre research` se lançait à la main. Un planificateur automatique ne doit ni consommer une occasion qui n'a pas eu lieu, ni exécuter deux fois la même chose, ni bloquer le cycle de décision.
- **Décision :** (1) les tâches (`intel_jobs`) et chaque décision de planification (`intel_job_runs`) sont persistantes. (2) `next_run_at` n'avance que si la tâche a réellement tourné ou échoué : un arrêt d'urgence, un bail tenu par un autre exécutant ou un créneau déjà exécuté la laissent due. (3) Un **bail** empêche deux exécutants simultanés ; un bail expiré est un plantage récupéré (la ligne RUNNING devient ABANDONED). (4) `(tâche, créneau d'une minute)` est unique : une tâche ne peut pas s'exécuter deux fois dans la même minute. (5) Un échec est journalisé, annule les écritures de la tâche et recule (jamais plus tôt que le rythme normal, plafonné à 1 h). (6) Le worker appelle la tâche entre la synchro et le cycle ; une erreur du planificateur est journalisée et le cycle continue. (7) `request_run` rend une tâche due sur un événement ; aucune source d'événement n'y est encore branchée.
- **Raisons :** reprise après incident et idempotence sans dépendre de la mémoire d'un processus ; l'arrêt d'urgence reste le dernier mot.
- **Compromis :** une seule tâche (`research`) ; la résolution du créneau est d'une minute ; les budgets par fournisseur restent ceux du Budget Controller, appliqués à l'intérieur de la passe ; le déclenchement événementiel est une API sans émetteur.
- **Conséquences :** migration `0005` ; `ambre scheduler-status` ; réglages `intelligence.schedule_research`, `research_interval_seconds` (600 s), `job_lease_seconds`. Les tests ne peuvent atteindre aucun fournisseur réel (garde dans `conftest.py`).

## ADR-015 — Recherche universelle : repli généraliste, états d'information, file d'attente et couverture

- **Contexte :** 2 marchés sur 1 153 étaient collectables, les autres étaient compris mais sans issue faute de connecteur spécialisé. L'audit montre que les chiffres étaient exacts (1 094 sans connecteur, 57 clos non résolus, 2 collectables) et que le scheduler et le cache fonctionnaient comme prévu.
- **Décision :** (1) un marché sans connecteur spécialisé mais ayant un **sujet** cherchable (entité principale et termes propres) bascule sur une recherche généraliste de presse (niveau 2, GDELT). Le niveau 2 produit des indices : il n'est **jamais estimable**. (2) Les marchés du même domaine et de la même entité partagent une recherche ; la pertinence se juge par marché, sans fusion des critères. (3) Douze **états d'information** par marché, recalculés à chaque passe et stockés (`intel_market_state`, migration `0006`) ; aucun n'est définitif et `READY_FOR_PREDICTION` exige preuve, question claire et modèle validé. (4) Une **file d'attente** classe les groupes (spécialisés d'abord, puis par incertitude), limite les appels par passe et attend brièvement l'intervalle d'un fournisseur sans jamais le contourner ; rien n'est abandonné. (5) Un rapport de **couverture** réconcilie chaque marché non résolu avec son état, avec les dénominateurs. (6) Chaque recherche est mémorisée dans le Cognitive Vault. (7) Domaines `HEALTH` et `CRYPTO` ajoutés.
- **Raisons :** être « intelligent » en recherche, c'est chercher selon le besoin du marché, pas selon les API installées ; mais ce qu'on trouve par la presse n'est ni vérifié ni une prédiction.
- **Compromis :** un seul fournisseur généraliste, soumis à une limite de débit stricte et, le 2026-11-02, refusant l'IP (HTTP 429) : la couverture réelle par la presse n'est donc pas encore démontrée. Le niveau 3 (investigation) n'est pas implémenté. Les sources officielles nommées par un marché ne sont pas interrogées directement. Un fournisseur manquant ou en pause est signalé une fois par passe, pas par ligne de journal.
- **Conséquences :** `InfoNeed.GENERAL_NEWS`, charge utile `NewsArticlePayload`, `ambre coverage`, réglages `max_calls_per_pass`, `max_wait_in_pass_seconds`, `news_timespan`, `news_max_records`. Aucune décision, position ni écriture du ledger n'est produite (testé).

## ADR-016 — Backtest chronologique minimal, indépendant des simulations

- **Problème :** `evaluate` ne score que les marchés suivis pendant une simulation ; rien ne mesure la baseline sur l'historique déjà synchronisé, ni ne garantit que le taux de base n'a pas vu l'issue qu'il prédit.
- **Options :** réutiliser `evaluate` ; validation croisée aléatoire ; rejeu chronologique avec réajustement à chaque observation.
- **Choix :** rejeu chronologique. Une observation par marché (premier snapshot, comme ADR-007) ; le taux de base n'utilise que les résolutions connues strictement avant l'instant de décision.
- **Raisons :** une validation aléatoire laisserait le futur informer le passé ; le réajustement par observation reproduit ce que le système aurait su.
- **Compromis :** O(n²) sur le nombre de marchés ; premier prix seulement ; la baseline testée n'est pas un modèle appris. Aucun protocole de graine ni de découpage encore.
- **Conséquences :** nouvelle commande `ambre backtest` (lecture seule). `evaluation_engine.backtest` reste pur ; la requête vit dans `infrastructure`. Aucune interdiction d'`AGENTS.md` n'est levée et aucun avantage sur le marché n'est affirmé.


## ADR-017 — Journal de caractéristiques : constituer le jeu d'entraînement avant tout modèle

- **Problème :** les estimations ne peuvent s'appuyer sur les sources que par un modèle validé hors échantillon (ADR-013), et la baseline actuelle fait moins bien que le prix (Brier +0,004, IC95 [+0,002 ; +0,006], 54 marchés, ADR-016). Or rien n'enregistre ce que le réseau savait d'un marché au moment où il le regardait : sans cet historique daté, aucun modèle ne pourra jamais être entraîné ni comparé avec et sans source.
- **Options :** reconstruire les caractéristiques après coup depuis les observations ; ne rien stocker et attendre un modèle ; journaliser à chaque passe ce qui était connu.
- **Choix :** table append-only `intel_feature_journal` (migration `0007`). À la fin de chaque passe de recherche, pour chaque marché collectable qui a des caractéristiques, on écrit : instant, domaine, besoin, niveau, estimabilité, **prix du marché à cet instant**, caractéristiques datées. Une ligne n'est écrite que si la connaissance a changé (empreinte) ; les caractéristiques qui ne grandissent qu'avec l'horloge (`*age_seconds`) sont stockées mais exclues de l'empreinte.
- **Raisons :** reconstruire après coup risque une fuite (une observation arrivée après la décision) ; le prix à l'instant est nécessaire pour comparer au marché ; l'empreinte évite une ligne toutes les dix minutes.
- **Compromis :** les marchés sans caractéristiques ne sont pas journalisés (le groupe témoin vient du backtest) ; seuls trois domaines produisent des caractéristiques aujourd'hui (météo, séismes, presse) ; l'empreinte est exacte, pas tolérante aux variations négligeables.
- **Conséquences :** `intelligence.feature_journal` est pur ; `store.append_feature_journal` écrit. Aucune lecture sur le chemin de pari, aucun lien avec décisions, positions ni ledger (testé). Ce journal ne produit aucune probabilité. Étape suivante : un moteur d'expériences qui joint le journal aux issues résolues et compare le Brier avec et sans caractéristiques, sur des marchés non vus.
