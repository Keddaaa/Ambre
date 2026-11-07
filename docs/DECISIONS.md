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
- **Addendum du 2026-11-02 — résilience des sources de presse :** GDELT, seule source de presse, répond HTTP 429 de façon irrégulière depuis l'IP du projet (3 échecs enregistrés, aucun succès ; des appels isolés espacés de plus de 30 s refusés aussi) : 1 522 marchés restaient différés par cette seule cause. (1) Les sources lues **une fois pour tous les marchés** (flux, API de dernières nouvelles) sont séparées de la recherche par sujet : nouveau besoin `news_feed`, nouvelle tâche planifiée `news_ingest`, indépendante de `research` (chacune dans sa transaction, avec bail, arrêt d'urgence et journal). (2) Chaque source est relevée une fois, avec le Budget Controller, les pauses et reculs existants et une fenêtre de fraîcheur ; une source en panne, en pause ou sans quota est consignée et ignorée sans arrêter les autres. (3) Les articles stockés sont rapprochés localement de tous les marchés par la règle existante (`topic_relevant`), et jamais utilisables pour une prédiction à eux seuls. (4) Les articles sont regroupés en événements (URL canonique, puis titres proches dans 72 h) pour compter des histoires distinctes, pas des copies. (5) **Ajouts à la liste blanche, à confirmer par le propriétaire :** `spaceflight_news` (vérifiée, activée) ; `bbc_news_world`, `guardian_world`, `dw_top_stories` (déclarées mais `UNVERIFIED` et désactivées : leurs conditions n'ont pas pu être lues). GDELT n'a pas été sondé davantage et son comportement de pause est inchangé.
- **Limites constatées :** un flux RSS ne donne que titre, résumé et lien, pas le texte ; la couverture de Spaceflight News est spatiale ; la règle de rapprochement acceptait un mot de condition générique (« close ») et a produit une fausse correspondance dans la mesure réelle : corrigée (`GENERIC_TERMS` dans `queries.py`, jamais plus laxiste que l'ancienne règle) ; aucune source ne dit quand elle a vu l'article, donc `available_at` est l'instant de la relève.

- **Addendum du 2026-11-04 — rapprochement titre / marché :** sur 26 liens mesurés, une entité seule suffisait quand le marché n'avait pas de terme exploitable, et un verbe partagé reliait deux sujets (« China » + « launch » vers une invasion de Taïwan). Un marché qui nomme un autre acteur d'au moins 4 caractères exige désormais cet acteur, ou deux de ses termes (son seul terme s'il n'en a qu'un), dans le titre. Les noms plus courts (« US », « USA ») restent un décor. « This », « YES » et assimilés ne sont plus des entités. Un marché décrit par au moins 4 termes en exige deux dans le titre, et « center » devient un mot générique : sur les 26 liens réels, 17 sont écartés et 9 conservés. Les liens stockés sont invalidés sans être supprimés (ADR-021).
- **Addendum du 2026-11-04 — conditions des éditeurs :** les conditions officielles ont pu être lues (`API_CATALOG.md`). BBC : pas de prélèvement de métadonnées sur les flux RSS, permission requise pour l'IA et l'analyse informatique, `robots.txt` fermé aux agents d'Anthropic. Guardian : usage personnel et non commercial, pas d'outil automatisé, pas d'usage IA, d'agrégation ni de fouille sans accord écrit. DW : aucune condition propre aux flux trouvée. **Décision :** `bbc_news_world`, `guardian_world` et `dw_top_stories` restent `UNVERIFIED` et désactivés, et ne s'activent qu'avec une autorisation applicable à l'usage d'Ambre. Le point (5) de l'addendum du 2026-11-02 est clos pour ces trois flux.

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


## ADR-018 — Évaluation historique sans fuite, avant tout modèle contextuel

- **Problème :** attendre que les marchés ouverts se résolvent donne des échantillons minuscules (54 marchés, tous récents) dont la conclusion (« la baseline est moins bonne que le prix ») ne se généralise pas.
- **Options :** attendre ; scorer l'historique avec les réponses des sources d'aujourd'hui ; reconstruire le prix passé depuis l'historique des paris et n'évaluer que ce qui était réellement connu.
- **Choix :** reconstruire, pour des marchés résolus échantillonnés dans le temps, le prix à un âge donné depuis `GET /v0/bets` (`probAfter` du dernier pari antérieur), ajuster les modèles dans l'ordre chronologique sur les seuls marchés résolus avant l'instant de décision, comparer le prix, la baseline et un prix recalibré sur les mêmes marchés, et rapporter deux intervalles (naïf, par grappes de questions presque identiques). Les modèles contextuels restent `NOT_EVALUATED` : on ne fabrique aucune observation historique.
- **Raisons :** le prix passé est un fait reconstructible sans estimation ; une source obtenue aujourd'hui n'a pas été connue alors ; des marchés presque identiques ne sont pas des preuves indépendantes.
- **Compromis :** l'échantillon n'est pas aléatoire ; l'âge comme point de décision écarte les marchés rapides des longs horizons ; l'approximation normale est fragile sous ~30 grappes ; le coût est d'environ une requête GET par marché.
- **Conséquences :** `ambre history-universe`, `history-fetch`, `history-eval` (lecture seule côté Manifold), migration `0009`, primitive neutre `domain/similarity.py` (un moteur quantitatif ne dépend pas d'`intelligence`, règle testée). Résultat du 2026-11-02 : aucun modèle ne se distingue du prix (tous verdicts « inconclusive ») ; le résultat du backtest à 54 marchés est infirmé comme tendance générale.
- **Mise à jour du 2026-11-02 (lot de 5 663 marchés, correction pour comparaisons multiples) :** le verdict repose sur un intervalle par grappes élargi pour le nombre de comparaisons du rapport (Bonferroni, 8 ici), pour qu'un horizon chanceux ne soit pas déclaré meilleur. Une analyse par année accompagne chaque horizon. Résultat : la baseline AMBRE reste indistinguable du prix ; le prix recalibré est « better » à 3 jours seulement (−0,0030 de Brier, signe stable sur quatre années), inconclusif et instable aux autres horizons. Cela décrit un biais de prix de 5 à 8 points, pas un rendement : rien ne justifie à ce jour de miser sur cette base.


## ADR-019 — Test en avant du prix recalibré (pré-enregistré)

**Écrit avant le premier pari.** Ce qui suit est fixé maintenant et ne se modifie pas pendant le test.

- **Hypothèse H1.** Pour les marchés binaires `cpmm-1` en `MANA`, âgés de 2 à 4 jours, le prix `q` surestime la probabilité de `YES` : `P(YES) = sigmoid(−0,3181 + 0,9372 · logit(q))`.
- **Origine, et pourquoi elle ne prouve rien.** ADR-018 : à 3 jours, 4 474 observations, écart de Brier −0,0030, intervalle corrigé [−0,0056 ; −0,0005]. L'horizon de 3 jours a été retenu **après** avoir examiné quatre horizons, et les paramètres sont ajustés sur ces mêmes données. Le résultat historique n'est donc pas une preuve ; ce test sur des marchés qui n'ont pas encore eu lieu en est la vérification.
- **Règle gelée.** Modèle `recalibrated_price` (`prediction_engine/recalibration.py`) avec les paramètres ci-dessus, fenêtre d'âge `[2 ; 4]` jours, incertitude 0,03 ; hors fenêtre il reprend le prix et ne parie donc pas. Stratégie `ValueHunter` inchangée : Kelly fractionné (0,25) sur la probabilité pénalisée par l'incertitude, puis plafonné par le moteur de risque. Limites de risque identiques à la configuration par défaut (position 2 %, exposition 20 %, edge minimum 0,03). Capital 1 000 Mana.
- **Coût explicite.** `fee_rate = 0,02`, débité du registre simulé sur chaque mise (vérifié par test). Les paris Manifold observés ne montraient aucun frais : 2 % est un stress délibéré, pas un tarif. L'impact de prix vient de la simulation CPMM.
- **Isolation.** Configuration `configs/forward_test.yaml`, base séparée `var/forward_test.db`, mode `PAPER` uniquement ; recherche et presse désactivées dans cette instance. La simulation principale n'est pas modifiée.
- **Critère principal (une seule comparaison, niveau 95 %).** Sur la **première décision de chaque marché prise dans la fenêtre d'âge**, pour les marchés résolus `YES`/`NO` après cette décision : différence appariée de Brier (modèle − prix), intervalle par grappes de questions presque identiques, `z = 1,96`. **Confirmé** si la borne haute est négative. **Réfuté** si la borne basse est positive. Sinon **non confirmé**. Aucun verdict avant **300 marchés résolus** et **30 grappes** au moins.
- **Critère secondaire, descriptif seulement.** Résultat simulé en Mana frais compris, perte maximale, nombre de paris. Ce n'est pas un critère de succès : un test sur quelques centaines de marchés ne dit rien d'un rendement.
- **Règles de conduite.** Pas de lecture du critère principal avant 300 marchés résolus (on ne s'arrête pas quand l'intervalle nous plaît). Un contrôle de sécurité (erreurs, état de survie, arrêt d'urgence) reste permis. Aucun paramètre ne change : toute modification est un nouveau test, avec sa propre ADR. Si l'hypothèse n'est pas confirmée, on le dit tel quel.
- **Consigne d'exploitation (ajoutée avant tout pari, au premier cycle à blanc).** Lancer avec `MAX_MARKETS_PER_CYCLE=5000` : avec le plafond par défaut de 200 marchés par cycle, aucun des 31 marchés éligibles de 2 à 4 jours présents n'était évalué (les 200 plus récemment observés étaient tous plus âgés que 4,8 jours). Ce réglage ne change ni le modèle, ni les paramètres, ni les coûts ; il fixe seulement la couverture. Les marchés qui entrent dans la fenêtre pendant que le worker est arrêté sont manqués : la couverture n'est pas totale.
- **Limites annoncées.** Les marchés de la période de test seulement (dépendance au régime) ; liquidité simulée par la formule CPMM, sans carnet d'ordres ; le frais de 2 % est supposé ; la durée jusqu'à 300 marchés résolus est inconnue et dépend de la vitesse de résolution (à mesurer, pas à promettre).
- **Conséquences.** Nouveaux : `RecalibrationConfig`, `PredictionConfig.model` (défaut inchangé : `base_rate_shrinkage`), `pipeline.build_model`, déplacement du calibrateur dans `prediction_engine`. Aucune interdiction d'`AGENTS.md` n'est levée.

## ADR-020 — Écritures de l'API : pas de session, donc Origin, type de contenu et Host

- **Contexte :** l'API n'a ni session, ni cookie, ni authentification. `POST /control/run-cycle` ne lit pas de corps : FastAPI ne contrôlait donc pas son type de contenu, et n'importe quelle page web ouverte sur la machine pouvait lancer un cycle par une requête simple (mesuré : 200). `halt`, `resume` et `orders` refusaient déjà ces requêtes (422). En revanche aucune route ne contrôlait `Host` : un DNS rebinding pouvait lire et écrire sur toutes.
- **Décision :** (1) `WriteGuard` (`api/security.py`) s'applique à toute méthode non sûre, donc aux routes futures : `Origin` refusée si présente et hors `cors_origins` (403), `application/json` exigé (415). (2) `TrustedHostMiddleware` sur toutes les requêtes, `api_allowed_hosts` = `localhost`, `127.0.0.1` (400). (3) `/control/run-cycle` et `ambre cycle` prennent le bail du worker : 409 ou code 3 si un cycle tourne.
- **Compromis :** pas de jeton : le dashboard appelle l'API depuis le navigateur, et un jeton public dans le bundle ne protégerait rien. **Limite :** tout processus local peut encore écrire. L'API doit rester sur `127.0.0.1` ; l'exposer exige une vraie authentification.
- **Précision du 2026-11-05 :** le worker garde son bail pendant qu'il dort, donc un cycle manuel est refusé tant qu'il tourne. Ce refus est conservé : libérer le bail laisserait un cycle manuel retarder le réveil du worker. Le 409 dit désormais si le blocage vient d'un worker actif ou d'un autre cycle.

## ADR-021 — Invalidation tracée des liens marché / article

- **Contexte :** 17 des 26 liens stockés ne passent plus la règle de rapprochement. Certains sont cités par des décisions passées.
- **Décision :** `ambre relink` rejuge les liens `news_mention` actifs, à blanc par défaut ; `--apply` remplit `invalidated_at`, `invalidated_reason`, `invalidated_by` (migration `0011`). La ligne et ses raisons d'origine restent. Un lien invalidé ne compte plus (observations du marché, couverture, métriques) et `save_link` ne le recrée pas. Une décision antérieure à l'invalidation continue de le citer, signalé ; une décision postérieure ne le cite pas. Aucune table de décision, de position ou de registre n'est écrite.
- **Recalcul :** la tâche `news_ingest` rapproche de nouveau les articles stockés avec la règle en vigueur.
- **Limite :** une invalidation n'est pas annulée par une règle future plus large : il faudrait une commande dédiée.

## ADR-022 — Historique au niveau du pari et protocole d'apprentissage pré-enregistré

**Écrit le 2026-11-05, avant tout résultat de modèle sur les données corrigées.** Ce qui suit ne se modifie pas après lecture des résultats : un changement est une nouvelle expérience, signalée comme telle.

- **Constat.** `history_from_bets` écartait tout pari `isCancelled`. Or un ordre limite partiellement exécuté bouge le prix avant l'annulation du reste : ces mouvements manquent dans les 331 051 points (exemple : `chlzpuPE2n` s'y arrête à 0,155, le prix réel finit à 0,99). `market_price_points` et les résultats d'ADR-018 en héritent. Le paramètre `amount` est aussi une valeur finale : il contient les exécutions postérieures au pari.
- **Décision de données.** Filtre corrigé : une annulation n'est ignorée que si le prix n'a pas bougé. Nouvelles tables `market_bets`, `market_bet_state` (migration `0012`, commande `ambre history-bets`, GET seulement) : prix avant et après, montant échangé à l'instant du pari d'après les `fills`, clé anonyme du parieur. `market_price_points` reste intact : ADR-018 reste reproductible et la comparaison ancien / corrigé est possible. **ADR-019 n'est pas touché** : ni sa configuration, ni ses paramètres, ni son critère. Il utilise les prix en direct, mais l'origine de son hypothèse doit être remesurée.
- **Population.** Marchés de l'historique au niveau du pari, lecture complète, résolus `YES`/`NO`, au moins un pari. Un point de décision à l'âge `h` n'existe que si `created_at + h` précède la résolution et la clôture. Le filtre de clôture est conservé : une clôture antérieure à l'instant était observable à cet instant. Ce n'est jamais une variable.
- **Séparation.** Coupure `T = 2025-07-01 00:00 UTC`. **Entraînement** : créés et résolus avant `T`. **Validation** : créés dans `[T ; 2026-05-01[`. Un marché n'est donc jamais des deux côtés. Les marchés à cheval sur `T` et ceux créés depuis le 2026-05-01 (censure trop forte, et séparation d'avec ADR-019) ne servent nulle part. **Purge** : tout marché d'entraînement dont la question appartient à une grappe contenant un marché de validation (`cluster_texts`, seuil 0,6) est retiré de l'entraînement.
- **Horizons.** 1, 3, 7 et 30 jours, ceux d'ADR-018 : aucun nouveau choix. Un modèle par horizon.
- **Variables.** `feature_engine/historical_features.py` reçoit la date de création, les paris et l'instant : la clôture et la résolution ne lui sont pas transmises. Trajectoire : `logit_change_24h`, `logit_change_open`, `realized_volatility`. Activité : `log_trades`, `log_trades_24h`, `log_volume`, `log_volume_24h`, `log_bettors`, `log_hours_since_trade`, `logit_x_log_trades`. **Refusées (valeur finale)** : temps jusqu'à l'échéance, liquidité, volume et parieurs de `markets.raw`. L'âge est constant à horizon fixé. Une valeur absente reste `None` ; le modèle la remplace par la moyenne d'entraînement.
- **Modèles.** Prix du marché ; `ambre_baseline` (`BaseRateShrinkage`) ; `recalibrated` et les modèles à variables partagent une seule forme, `sigmoid(a + b·logit q + Σ w·x)`, ridge ancrée sur le prix (`a = 0`, `b = 1`, `w = 0`), logit borné à [0,005 ; 0,995], ajustée sur l'entraînement seul. `recalibrated` : sans variable. `price_path` : trajectoire. `full` : trajectoire et activité. Pénalité choisie dans {1, 10, 100, 1000} sur le dernier quart chronologique de l'entraînement. Les paramètres gelés d'ADR-019 ne sont pas utilisés : ils ont été ajustés sur la période de validation.
- **Mesures.** Brier, log loss, calibration (10 classes, erreur moyenne pondérée), sur les mêmes marchés. Différences appariées, intervalle par grappes (CR1), élargi par Bonferroni sur **24 comparaisons** : à chaque horizon, quatre modèles contre le prix, `price_path` et `full` contre `recalibrated`.
- **Critère.** Un modèle est `candidate_for_shadow` à un horizon seulement si son Brier bat, intervalle corrigé à l'appui, le prix **et** `recalibrated`, si sa log loss n'est pas pire que celle du prix, et si le signe contre le prix tient sur les deux moitiés de la validation. Sinon : « pas de preuve ». Ce statut n'autorise qu'une exécution shadow, sans mise.
- **Limites annoncées.** La validation n'est pas vierge : ADR-018 a lu cette période, même avec des prix fautifs. Seules les nouvelles variables n'y ont jamais été regardées. Échantillon de marchés déjà résolus (censure estimée de 10 à 28 % sur la validation, d'après les durées de vie 2023-2024). Aucune mesure de rendement. Le seul test réellement hors échantillon sera l'exécution shadow sur des marchés à venir.
