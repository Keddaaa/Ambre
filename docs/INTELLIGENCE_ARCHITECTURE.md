# Global Intelligence Network (GIN) — architecture

État : **conception (phase A)**. Aucun module `intelligence` n'existe encore ; chaque sous-module est créé avec son premier code et ses tests, jamais comme dossier vide (`AGENTS.md`). Décision : ADR-010. Fournisseurs vérifiés : `API_CATALOG.md`. Limites : `API_LIMITS.md`. Licences : `DATA_LICENSES.md`.

## Objectif et garde-fous

AMBRE s'informe sur le monde pour réduire l'incertitude de ses estimations (ADR-004 : sans information externe, il ne peut que répéter le prix). Le réseau d'intelligence **produit des signaux structurés**. Il ne décide jamais d'une mise.

- Une information, même confirmée, passe par le Prediction Engine, le Value Engine, puis le Risk Engine, qui garde le dernier contrôle. Aucune publication non vérifiée ne déclenche une transaction.
- Lecture seule, mode `PAPER` uniquement, aucun endpoint d'écriture (Manifold ou autre), aucune création de compte, aucun achat de crédits, aucun secret dans le code ni dans les journaux.
- Tout contenu collecté est une **donnée**, jamais une instruction. Un texte de page n'est jamais exécuté, ni transmis tel quel à un modèle sans filtrage.
- Une valeur inconnue reste `None`. Les quatre horloges ne se confondent pas : événement, publication, **disponibilité** (`available_at`), récupération. Une expérience hors échantillon n'utilise que ce qui était disponible avant la décision.

## Découpage et règle de dépendance

La règle d'`AGENTS.md` s'étend : les modules **purs** n'importent ni `infrastructure` ni `integrations` ni le réseau.

| Sous-module (`src/ambre/intelligence/`) | Rôle | Pur ? | Phase |
|---|---|---|---|
| `models` | contrats Pydantic : `Observation`, `ProviderCard`, `ResearchRequest`, statuts | oui | B |
| `registry` | registre, configuration YAML, santé, activation | non (lit la config, la base) | B |
| `providers/<nom>` | un client HTTP par fournisseur, respectant le contrat commun | non (réseau) | C |
| `normalization` | réponse brute → `Observation` (quatre horloges, provenance) | oui | C |
| `budget` | Budget Controller : quotas, réserves, recul, pause | oui (état injecté) | B |
| `ingestion` | exécute une requête planifiée, journalise, stocke | non | B–C |
| `deduplication`, `events`, `verification` | regroupement, indépendance des sources, statuts | oui | D |
| `market_matching` | lie événements et marchés (critères de résolution lus) | oui | D |
| `planning`, `routing`, `value` | Information Planner, Source Router, Information Value Estimator | oui | E |
| `evidence` | Evidence Evaluator | oui | E |
| `memory` | Research Memory (requêtes, résultats, coûts) | non (base) | E |
| `learning` | classement statistique des sources, bandit plus tard | oui | F |
| `scheduling` | exécution périodique bornée par les budgets | non | B/E |

Les fournisseurs sont interchangeables : aucun module métier n'en dépend directement. Aucun module ne dépend d'un seul fournisseur.

## Contrat de fournisseur

```text
get_capabilities() -> Capabilities        # ce qui est réellement pris en charge
healthcheck() -> Health                   # OK / DEGRADED / DOWN, jamais une valeur inventée
get_rate_limit_status() -> RateLimit      # restant, remise à zéro ; UNKNOWN si le fournisseur n'en publie pas
fetch_latest(...) -> Result[Observation]
search(query, ...) -> Result[Observation]
fetch_historical(start, end, ...) -> Result[Observation]
```

Une méthode non prise en charge renvoie `Unsupported(reason)`. Un résultat est `Ok`, `Empty` (réponse valide sans donnée), `Inconclusive` (par exemple le corps `{}` de GDELT, qui ne dit ni « rien » ni « erreur ») ou `Failed(kind)`. Jamais une liste vide qui se fait passer pour un succès.

## Modèle de données commun

`Observation` : `observation_id`, `provider_id`, `domain`, `source_url`, `title`, `summary`, `entities`, `countries`, `languages`, `event_at`, `published_at`, `available_at`, `ingested_at`, `verification_status`, `source_reliability`, `raw_payload_ref`. Tous les champs de temps et de fiabilité sont optionnels. Exemples vérifiés :

- GDELT : `seendate` devient `available_at` ; `published_at` reste `None`.
- USGS : `time` = `event_at`, `updated` = dernière révision. Une révision crée une nouvelle observation liée à la précédente, sans réécrire l'historique.
- Open-Meteo : une prévision a une heure d'émission (`ingested_at`) et une cible (`event_at`). Ce n'est pas une mesure.

Le stockage est relationnel (PostgreSQL via SQLAlchemy et Alembic, comme le reste). Pas de base graphe tant qu'un usage ne le justifie pas.

## Moteur de recherche d'information adaptatif (phases E et F)

Objectif : décider **quoi chercher, où, quand actualiser et quand s'arrêter**, à coût maîtrisé. Il commence déterministe, puis se classe statistiquement, puis seulement ensuite apprend.

1. **Information Planner.** Pour un marché, déduit les informations manquantes depuis ses champs (catégorie, dates, critère de résolution, entités). Sortie : `ResearchRequest` avec `missing_information`, une priorité et un objectif explicite. Sans objectif défini, aucune recherche n'est lancée.
2. **Source Router.** Choisit les fournisseurs parmi ceux de la liste blanche selon pertinence pour le type d'information, fraîcheur, fiabilité, coût (quota consommé) et disponibilité (santé). Version 1 : table de correspondance déterministe type d'information → fournisseurs, écrite à la main et étiquetée `prior`.
3. **Information Value Estimator.** `expected_information_gain` reste `None` tant qu'il n'est pas mesuré. Version 1 : un classement relatif (incertitude restante du marché × pertinence a priori × âge de la donnée en cache ÷ coût en quota), jamais présenté comme une probabilité ni comme un gain mesuré. Version 2 : calibré sur la variation réelle du Brier avec et sans la source.
4. **Budget Controller.** Compteurs par fournisseur (minute, heure, jour), réserve pour les recherches prioritaires, recul exponentiel après 429, pause automatique. Une recherche secondaire n'entame jamais la réserve. Aucun appel payant.
5. **Evidence Evaluator.** Regroupe les reprises d'une même dépêche en **une** preuve, mesure l'âge, détecte les contradictions, attribue `CONFIRMED`, `CORROBORATED`, `UNVERIFIED`, `CONTRADICTED`, `OUTDATED` ou `RETRACTED`. La popularité (likes, scores, rang) n'entre pas dans la vérité.
6. **Research Memory.** Journalise chaque recherche : objectif, fournisseurs interrogés, coût en quota, résultats, âge, décision ultérieure. Évite de refaire une recherche dont le résultat reste valable.
7. **Règle d'arrêt.** On s'arrête dès que : (a) le meilleur candidat restant a un score par unité de quota sous le seuil, (b) la réserve prioritaire serait entamée, (c) le plafond de requêtes par marché et par jour est atteint, ou (d) le marché n'a plus d'information manquante. Les seuils sont des paramètres de configuration versionnés, pas des valeurs apprises en phase E.
8. **Research Learning Engine.** Phase F. Classement statistique des sources par contribution mesurée, puis, **seulement si les données le permettent**, un bandit contextuel. Pas de bandit avant d'avoir un protocole validé et des marchés résolus en nombre suffisant.

Une sélection par LLM n'est jamais une mesure : une confiance déclarée par un modèle ne remplace pas un résultat mesuré.

## Protocole d'évaluation

Configurations comparées, sur les mêmes marchés et les mêmes dates :

| | Données |
|---|---|
| A | Manifold seul (baseline actuelle) |
| B | A + actualités |
| C | A + données structurées spécialisées |
| D | A + réseaux sociaux |
| E | toutes les sources retenues |

Métriques : Brier, log loss, calibration (`evaluation_engine` existe déjà), rendement simulé et drawdown, coût de collecte (quota), latence de détection, taux de données manquantes. Les décisions sont comparées par différence appariée avec intervalle, comme dans ADR-007. Une décision n'utilise que des observations dont `available_at` précède sa date. Tant que n < 30 marchés résolus, aucune conclusion (`sufficient_sample`). Les décisions manuelles (`actor` ≠ `AMBRE`, ADR-009) sont exclues de la comparaison.

## Limites connues (2026-11-02)

- Aucun fournisseur vérifié n'offre à lui seul la résolution des marchés Manifold : ce sont des signaux, pas des vérités.
- La recherche par mots-clés sur Bluesky exige un compte (`API_CATALOG.md`) : non créé.
- GDELT limite à une requête toutes les 5 s et renvoie parfois `{}` : la collecte d'actualités est lente et ses résultats vides sont non concluants.
- Open-Meteo est réservé à un usage non commercial.
- Plusieurs fournisseurs du catalogue du propriétaire restent `UNVERIFIED`.

## Phases

| Phase | Contenu | État |
|---|---|---|
| A | audit du dépôt, API vérifiées, 4 documents, ADR-010 | ce document |
| B | modèles, registre, budget, journalisation, stockage, tests | à faire |
| C | connecteurs, un par un, avec fixtures `synthetic_` et un test de santé réel hors suite de tests : Open-Meteo, USGS, EONET, Hacker News, GitHub, GDELT, Bluesky (comptes publics) | à faire |
| D | déduplication, événements, vérification, rapprochement avec les marchés | à faire |
| E | recherche active adaptative (planner, router, valeur, mémoire, budget) | à faire |
| F | mesure de la contribution des sources, classement, bandit éventuel | à faire |
| G | pages du dashboard : registre, flux, connexions, performance, budget, requêtes, mémoire | à faire |
