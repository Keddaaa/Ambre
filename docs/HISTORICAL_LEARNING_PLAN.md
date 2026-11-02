# Historical Learning Engine : audit et plan

État au 2026-11-02. Ce document est un **plan**. Rien de ce qu'il décrit n'est implémenté, et aucune performance n'y est validée. L'expérimentation qu'il propose est **indépendante du test en avant ADR-019**, qui n'est modifié en rien.

## 1. Ce qui existe et se réutilise

| Brique | Rôle | Pur |
|---|---|---|
| `evaluation_engine/history.py` | prix d'un marché à un instant passé, depuis les paris (`probAfter` du dernier pari antérieur) | oui |
| `evaluation_engine/historical.py` | points de décision par âge, ajustement chronologique, comparaison appariée, intervalle par grappes, correction pour comparaisons multiples, stabilité par année | oui |
| `prediction_engine/calibration.py` | régression logistique ridge ancrée sur l'identité, sans dépendance externe | oui |
| `domain/similarity.py` | grappes de questions presque identiques | oui |
| `data_engine/history_fetch.py` | lot de marchés résolus échantillonné dans le temps, historiques de paris (GET seulement) | non |
| `evaluation_engine/forward.py` | critère mécanique qui masque son verdict sous un seuil d'échantillon | oui |
| `feature_engine/features.py` | vecteur de caractéristiques daté (`FeatureSpec` décrit chaque variable) | oui |

## 2. Données réellement disponibles (mesurées sur `var/ambre.db`)

| Donnée | Volume | Connue à l'instant de décision ? | Utilisable |
|---|---|---|---|
| Marchés binaires résolus `YES`/`NO` avec historique complet | 5 663 | — | étiquettes : oui |
| Points de prix (`probAfter` après chaque pari retenu) | 331 051 ; médiane 27 paris par marché, 90e centile 164 | **oui** (horodatés) | oui |
| Création, question, créateur | 5 663 ; 1 761 créateurs, dont 102 avec au moins 10 marchés | **oui** (fixés à la création ; les modifications de question ne sont pas tracées) | oui |
| Résolution et date de résolution | 5 663 ; 2023 : 957, 2024 : 1 330, 2025 : 1 492, 2026 : 1 884 | après coup seulement | étiquettes et ordre chronologique |
| Date de clôture | 5 663 | **valeur finale** : peut avoir changé après la décision | à n'utiliser qu'avec précaution (voir 3.2) |
| Volume, parieurs uniques, liquidité, dernier pari, dernier commentaire, prix final (`markets.raw`) | 5 663 | **non** : lus après la résolution | **jamais comme caractéristique** |
| Montant, auteur et sens de chaque pari | 0 stocké (lus puis non conservés) | oui, s'ils étaient stockés | à récupérer (étape 1) |
| Groupes et thèmes (`group_slugs`) | 0 sur 5 663 (la liste de recherche ne les donne pas) | inconnu | non |
| Journal de caractéristiques contextuelles | 24 lignes, 0 marché résolu | oui | pas avant des mois |
| Observations externes (presse spatiale, séismes) | 54 et 139, aucune historique | seulement depuis le 2026-11-02 | non pour l'historique |

**Conséquence.** Un modèle appris aujourd'hui ne peut s'appuyer que sur le prix et son parcours, l'âge, l'activité de paris avant l'instant de décision (après l'étape 1) et l'historique du créateur sur ses marchés **déjà résolus**. Aucune information externe historique n'est disponible : un modèle « avec sources » reste non évaluable.

**Non-stationnarité.** Le taux de `YES` des marchés retenus baisse d'année en année (0,53 en 2023, 0,44 en 2024, 0,42 en 2025, 0,40 en 2026). Une partie peut venir de la censure (point 3.1) ; une autre peut être réelle. Tout modèle doit être jugé par période.

## 3. Fuites et biais identifiés dans le moteur actuel

1. **Censure de l'échantillon (importante).** Le lot ne contient que des marchés déjà résolus. Pour les créations récentes, les marchés longs manquent encore, ce qui peut déformer le taux de `YES`. *Remède proposé :* mesurer, par mois de création, la part de marchés résolus (liste `filter=all` contre `filter=resolved`), et n'évaluer strictement que les mois où elle dépasse un seuil fixé à l'avance (par exemple 95 %).
2. **Date de clôture finale (mineure).** `build_observations` exclut un point « clos avant la décision » d'après la date de clôture finale, qui a pu être modifiée après la décision. C'est une sélection qui lit le futur, sans toucher aux caractéristiques. *Remède :* ne plus filtrer sur la clôture, seulement sur la résolution, et mesurer l'effet ; ou retrouver la clôture d'origine si elle devient disponible.
3. **Choix d'horizon après coup (documenté).** L'horizon de 3 jours d'ADR-019 a été retenu après avoir vu quatre horizons. Ce point est déjà déclaré dans ADR-019 ; il ne doit pas se reproduire : les horizons et les variables se fixent avant l'évaluation finale.
4. **Mêmes marchés à plusieurs horizons.** Les comparaisons d'un rapport ne sont pas indépendantes. La correction de Bonferroni contrôle leur nombre, pas leur dépendance.
5. **Pas de rendement historique.** La liquidité d'un marché à un instant passé (taille des réserves CPMM) n'est pas connue : seul le prix l'est. Un backtest de gains ou de pertes simulerait donc des remplissages inventés. Le moteur reste un moteur d'exactitude probabiliste (Brier, log loss, calibration).
6. **Coût de calcul.** Réajuster un modèle à chaque observation coûte environ deux minutes pour 5 663 marchés avec un modèle à deux paramètres. Avec plus de variables, il faut réajuster par mois, pas par observation.

## 4. Indépendance vis-à-vis d'ADR-019

- Aucune modification de `configs/forward_test.yaml`, de `prediction_engine/recalibration.py`, de la stratégie `ValueHunter`, ni de l'instance Docker `ambre-forward`.
- Les données d'apprentissage s'arrêtent aux marchés **créés avant le 2026-10-30** : aucun marché du test en avant ne peut entrer dans l'apprentissage, ni par son prix ni par son issue.
- Les résultats de l'Historical Learning Engine ne sont jamais versés dans le critère d'ADR-019, et ADR-019 n'est jamais relu pour régler l'Historical Learning Engine.
- Tables propres (`hle_*`), commandes propres (`ambre hle-...`), aucun import depuis `pipeline.py` ni depuis une stratégie.

## 5. Plan par étapes

Chaque étape suit la procédure d'`AGENTS.md` (test qui échoue d'abord, ADR si structurant) et se termine par une mesure réelle documentée.

### Étape 0 — Cadre (ADR-021)
Fixer avant toute mesure : le coffre final, les horizons, la liste des variables, les métriques, le budget de comparaisons. **Coffre proposé** : les marchés créés sur une période récente mais déjà presque entièrement résolue (à choisir d'après la mesure de censure de l'étape 1), jamais lus avant l'évaluation finale. Développement sur la période antérieure seulement.

### Étape 1 — Compléter les données sans fuite
- Stocker pour chaque pari : montant, sens, auteur anonymisé (empreinte), annulation, rachat (table `hle_bets`, migration). Une nouvelle lecture des 5 663 historiques coûte environ 5 800 requêtes GET (environ 25 minutes au rythme mesuré).
- Mesurer la censure par mois de création (point 3.1) ; en déduire la population d'évaluation stricte.
- Tests : un pari postérieur à l'instant ne change aucune donnée calculée à cet instant ; données manquantes à `None`, jamais 0.

### Étape 2 — Caractéristiques historiques datées (module pur)
- `feature_engine/historical_features.py`, sur le modèle de `FeatureSpec`, avec une étiquette de disponibilité pour chaque variable : `POINT_IN_TIME`, `CREATION_TIME` ou `FINAL_VALUE`. Toute variable `FINAL_VALUE` est refusée par construction.
- Premières variables candidates : logit du prix ; variation du prix sur 24 h et 7 jours ; volatilité réalisée ; nombre de paris, volume et parieurs distincts avant l'instant ; temps depuis le dernier pari ; âge ; taux de `YES` passé du créateur, calculé seulement sur ses marchés résolus avant l'instant (avec un lissage vers le taux global) ; type de question (« avant telle date », numérique, personnel), extrait du texte.
- Tests de non-fuite par propriété (Hypothesis, déjà en dépendance) : ajouter n'importe quel pari postérieur ne change aucune variable.

### Étape 3 — Protocole d'expérience
- Réajustement par mois (fenêtre croissante), avec les seuls marchés résolus avant le début du mois évalué.
- Une observation par marché et par horizon fixé à l'avance ; intervalles par grappes et correction pour le nombre total de comparaisons (déjà disponibles).
- **Registre d'expériences** append-only (`hle_experiments`) : empreinte de la configuration, version du code (`git` SHA), empreinte des données, résultats. Une expérience non enregistrée n'existe pas.
- **Budget de comparaisons** : chaque lecture du coffre compte ; le coffre est lu une fois par famille de modèles.

### Étape 4 — Modèles candidats
- Références : prix du marché ; prix recalibré (deux paramètres).
- Candidat principal : régression logistique régularisée sur un petit nombre de variables de l'étape 2, écrite comme le calibrateur existant (sans dépendance externe). Des modèles plus riches (arbres, gradient boosting) demanderaient une nouvelle dépendance : décision du propriétaire.
- Critère : un candidat n'est retenu que s'il bat le **prix recalibré** (pas seulement le prix brut) sur le développement, puis sur le coffre, avec un intervalle corrigé qui exclut zéro, et sans changement de signe d'une année à l'autre.

### Étape 5 — Passage éventuel à un test en avant
Seulement par un **nouvel ADR pré-enregistré**, sur une instance et une base séparées, comme ADR-019, sans jamais modifier ADR-019 ni sa configuration.

## 6. Ce que ce plan ne promet pas

- Que le prix du marché soit battable : la mesure actuelle montre un écart faible et seulement à un horizon.
- Un rendement : les gains et pertes historiques ne sont pas calculables sans la liquidité passée.
- Un modèle « avec sources » : aucune donnée externe historique n'existe ; seul le journal de caractéristiques le permettra, dans plusieurs mois.

## 7. Décisions à prendre par le propriétaire

1. Ajouter ou non une dépendance de calcul (numpy, scikit-learn) pour l'étape 4.
2. Valider la période du coffre une fois la censure mesurée (étape 1).
3. Le budget de requêtes : environ 5 800 GET pour l'étape 1, puis la mise à jour périodique.
