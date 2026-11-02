# Universal Research Engine

Décision : ADR-015. Code : `src/ambre/intelligence/` (`queries.py`, `states.py`, `generic_planning.py`, `service.py`, `coverage.py`, `providers/gdelt.py`). Commande : `ambre coverage`.

Objectif : que l'absence de connecteur spécialisé ne soit pas une impasse. Un marché dont le sujet est cherchable est recherché par un moteur généraliste ; ce qui est trouvé est un **indice**, jamais une prédiction.

## Audit du 2026-11-02 (base réelle, avant la modification)

| | Marchés |
|---|---|
| Marchés en base | 1 279 |
| Résolus | 126 |
| **Non résolus, tous avec snapshot** | **1 153** |
| `NO_CONNECTOR` (compris, aucun fournisseur) | 1 094 |
| `NOT_APPLICABLE` (clos mais pas encore résolus) | 57 |
| `COLLECTABLE` (séismes) | 2 |

Les trois lignes somment à 1 153. Les « 48 marchés non expliqués » du diagnostic précédent sont en réalité les marchés **clos mais non résolus** : 57 à la date de l'audit, comptés à part parce qu'on n'y cherche plus rien. Le chiffre de 1 143 venait de l'API (`status=open`), pris à un autre moment.

Pourquoi 1 094 sans connecteur : 594 domaine `GENERAL` (aucun contrat spécialisé), 249 géopolitique, 152 technologie, 41 finance, 25 science, 18 sport, 13 économie, 2 météo. 66 questions n'ont aucune entité reconnue ; 747 ne donnent pas de période. Aucune de ces questions n'avait de source spécialisée accessible : l'exclusion était correcte, mais c'était une impasse.

Le scheduler et le cache étaient sains : un appel USGS partagé par les 2 marchés de séismes, réponse valable 10 min, tâche exécutée toutes les 10 min. Aucun intervalle n'a été modifié.

## Trois niveaux

1. **Spécialisé** : USGS (séismes), Open-Meteo (températures, pluie). Données structurées, estimables si le marché est clair.
2. **Généraliste** : GDELT (articles de presse), pour tout marché ayant un sujet cherchable. Preuves = articles liés au marché ; un marché de niveau 2 n'est **jamais estimable**.
3. **Investigation contextuelle** (décomposition en sous-questions, recoupement, contradictions) : **non implémentée**. Aucune ligne de code ne le prétend.

## Sujets et regroupement

Chaque marché reçoit un *sujet* : son entité principale (la plus parlante, pas un « US » nu) et ses propres termes. Les marchés du même domaine sur la même entité partagent une clé, donc **une seule recherche** (« OpenAI release model » sert « publier un modèle » et « annoncer un modèle »). La requête n'emploie que l'entité et les termes que tous les marchés du groupe ont en commun. Chaque marché juge ensuite la pertinence de chaque titre **avec ses propres termes** : l'information est partagée, la décision reste par marché, les critères de résolution ne sont jamais fusionnés.

Sans entité, la recherche utilise au plus 3 mots d'au moins 5 lettres ; sans cela, pas de sujet (le marché reste `NO_AVAILABLE_SOURCE`, pas une erreur).

## États d'un marché (12)

`NOT_ANALYZED`, `UNDERSTOOD`, `RESEARCH_PLANNED`, `RESEARCH_PENDING`, `RESEARCH_RUNNING`, `EVIDENCE_FOUND`, `INSUFFICIENT_EVIDENCE`, `NO_AVAILABLE_SOURCE`, `RESEARCH_DEFERRED`, `RESEARCH_FAILED`, `READY_FOR_PREDICTION`, `INSUFFICIENT_MODEL`.

Recalculés après chaque passe et stockés (`intel_market_state`). PENDING et RUNNING sont transitoires (le temps d'une passe) ; les autres sont comptés. Aucun état n'est définitif : un échec ou un report est retenté, une source manquante peut devenir disponible. Des preuves anciennes restent visibles quand un rafraîchissement est reporté. `READY_FOR_PREDICTION` exige preuve, question claire **et** modèle validé : il n'est atteignable aujourd'hui pour aucun marché, car aucun modèle n'est validé.

## File d'attente et coût

- Groupes classés : spécialisés d'abord, puis généralistes par incertitude du marché (4q(1−q)) décroissante.
- Au plus `max_calls_per_pass` (6) appels par passe ; le reste attend (`RESEARCH_DEFERRED`), rien n'est abandonné.
- Attente brève pour l'intervalle minimal d'un fournisseur, plafonnée à `max_wait_in_pass_seconds` (12 s). Aucun quota n'est contourné.
- Cache : 10 min pour USGS, 1 h pour Open-Meteo, 6 h pour une recherche de presse sur une semaine. Un cycle sans nouvelle donnée n'est pas un échec.
- Un fournisseur manquant, en pause ou sans budget est signalé **une fois** dans le rapport de passe (`provider_unavailable`), pas par ligne de journal et par sujet.
- Chaque appel est journalisé avec son coût (1 requête, 0 monnaie), ses sources refusées et ses raisons, et enregistré dans le Cognitive Vault comme note `EXPERIENCE`. Une panne du Vault n'interrompt jamais la recherche.

## Couverture (`ambre coverage`)

Chaque marché non résolu est dans **un** état ; les états somment au total (`reconciled`). Chaque pourcentage est donné avec son numérateur, son dénominateur et sa population. Les marchés jamais vus comptent comme `NOT_ANALYZED`. Le coût total est la somme des requêtes ; ce n'est pas une métrique de performance.

## Mesure réelle et limite actuelle

Une passe réelle (2026-11-02) : 1 158 marchés scannés, 1 093 deviennent `COLLECTABLE` par la recherche généraliste (contre 2 avant), 57 restent clos, 8 sans sujet cherchable. **Mais GDELT a répondu HTTP 429** à ce premier appel, puis à deux autres requêtes espacées de 12 s, puis à une requête brute (aucun délai indiqué). L'IP est limitée : aucune preuve de presse réelle n'a donc pu être obtenue ce jour-là, hormis un échantillon antérieur de la vérification de la phase A. Le code respecte la pause (recul 60 s doublé jusqu'à 1 h) et ne contourne rien. Les 1 090 marchés concernés sont `RESEARCH_DEFERRED`, pas bloqués.

Débit si GDELT répond : 6 appels toutes les 10 min, soit au plus 864 par jour (budget interne : 300 par heure, 2 000 par jour). Un balayage des ~1 090 sujets prend donc plus d'une journée, et une actualisation toutes les 6 h de tous les sujets n'est pas possible : l'ordre de priorité décide.

## Ce qui reste impossible ou non fait

- Niveau 3 (sous-questions, recoupement, contradictions) et plan de recherche proposé par un modèle de langage.
- Sources officielles nommées par les marchés (BLS, Fed, etc.) : **non interrogées directement**, faute de connecteur vérifié (FRED exige une clé : `REQUIRES_REGISTRATION`). Le rapport le dit explicitement.
- Les descriptions de marché ne sont pas stockées ; les critères de résolution réels restent non lus.
- Déclenchement par événement : l'API `request_run` existe, aucune source d'événement n'y est branchée.
- Aucun modèle de prédiction validé : aucun marché n'est `READY_FOR_PREDICTION`.
- Apprentissage de l'utilité des sources, règles et raccourcis de recherche appris, pages du dashboard.
- Dépendance à un seul fournisseur généraliste, actuellement limité en débit : un second (RSS officiels, autre API) reste à vérifier.

## Diagnostic du débit (2026-11-02, après les passes manuelles)

`ambre coverage` contient désormais une section `diagnostics`, dérivée uniquement de lignes enregistrées (jamais d'une cause déduite d'un agrégat).

**Réconciliation des 1 608 marchés non résolus** (états exclusifs, somme exacte) : 1 522 `RESEARCH_DEFERRED` + 1 `RESEARCH_FAILED` + 2 `INSUFFICIENT_EVIDENCE` + 14 `NO_AVAILABLE_SOURCE` + 69 `UNDERSTOOD` (68 clos en attente de résolution, 1 ambigu).

**Chevauchements, comptés à part :** « recherchables » (1 525) recoupe les états ci-dessus ; « recherchés » valait 3 parce qu'il comptait un échec (GDELT 429) avec deux réponses (USGS). Le rapport distingue maintenant `answered` (2) et `attempted_failed` (1).

**Pourquoi 1 522 marchés sont différés :** la cause est enregistrée par marché : `provider unavailable now (budget, pause or stop)`. GDELT répond HTTP 429, le fournisseur est mis en pause (recul exponentiel), et tous les groupes de presse attendent. Le plafond de 6 appels par passe n'est pas atteint. Les échecs ne sont pas mis en cache (seuls `OK` et `EMPTY` le sont) : aucun cache n'est faussement frais.

**Le scheduler n'a jamais tourné** : `intel_jobs` et `intel_job_runs` sont vides ; seules des passes manuelles (`ambre research`) ont eu lieu, et aucun worker n'est actif. Les compteurs ne mesurent donc pas un système laissé en marche.

**Plafond de la planification, à lui seul :** 6 appels toutes les 600 s = 36 appels/h, soit environ 25,6 h pour 922 sujets en attente, même si GDELT répondait toujours. C'est de l'arithmétique sur les réglages (`diagnostics.capacity`), pas une mesure.

**Partage des recherches :** 1 525 marchés pour 925 sujets (1,65 marché par sujet ; 748 sujets n'ont qu'un marché). En ignorant le domaine : 854 sujets, soit un gain possible limité (environ 8 %).

**Pourquoi aucune preuve :** les 137 observations USGS sont des séismes M4,5 à M6,4 de la dernière semaine ; aucun n'est en Nouvelle-Zélande ni en Californie. Les deux marchés séismes concernés portent sur une année entière : un flux de sept jours ne peut pas les résoudre. Zéro lien est donc le résultat correct, pas un défaut de rapprochement. Les caractéristiques journalisées (`quake.matching_event_count = 0`) disent l'absence d'événement, pas une probabilité.

**GDELT :** l'intervalle de 5 s est une restriction documentée du fournisseur (message du serveur) ; notre budget interne est plus strict (10 s, 300/h). Un appel isolé a abouti (HTTP 200) puis des appels identiques ont repris un 429 : la limitation par IP est irrégulière et ne dépend pas de notre cadence.

**Conclusion :** le blocage est d'abord opérationnel (aucun worker en marche) et externe (limite GDELT), pas un défaut d'ordonnancement. Aucune limite n'a été modifiée.
