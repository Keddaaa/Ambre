# Resolution Intelligence Engine et planificateur générique

Décision : ADR-013. Code : `src/ambre/intelligence/` (`resolution.py`, `domain_params.py`, `generic_planning.py`, `context.py`, `estimation.py`). Règles paramétrables : `configs/research_rules.yaml` et `configs/gazetteer.yaml`.

Objectif : qu'Ambre comprenne **ce qu'un marché demande vraiment** avant de chercher quoi que ce soit, et qu'il le dise quand il ne le sait pas.

## Ce qui est extrait d'une question

Moteur déterministe, sans modèle de langage : il ne peut donc pas inventer un critère absent.

| Élément | Comment | Si absent |
|---|---|---|
| Sujet | la question, nettoyée | — |
| Domaine | mots-clés de `research_rules.yaml` (mots entiers, insensible à la casse) | `GENERAL` ; égalité entre deux domaines : ambiguïté signalée |
| Entités | suites de mots capitalisés (hors mois et mots outils) | liste vide |
| Lieu | gazetteer (alias en mots entiers : « paris » n'est pas trouvé dans « comparison ») | `None` |
| Date et période | `on`, `by`, `before`, `in YEAR`, `by the end of YEAR`, dates ISO | `None` ; **la date de clôture n'est jamais utilisée à la place** |
| Seuils et unités | nombre + unité (°F, °C, %, mm, in, cm, magnitude, USD) | liste vide |
| Direction | exceed, above, at least, below, or more… | `None` + ambiguïté ; aucune condition YES/NO n'est dérivée |
| Conditions YES / NO | seulement pour un seuil unique et orienté | `None` |
| Source de référence | « according to X », « per X », ou un organisme connu | liste vide + manque signalé |

Toujours signalé : **la description du marché n'est pas stockée** (la synchro ne garde que la question). Les critères de résolution qui y figurent ne sont donc jamais lus. Un marché n'est jamais présenté comme prêt à être estimé sans que les éléments exigés par son domaine soient dans la question.

Distinctions maintenues : date de clôture du marché ≠ date de l'événement ; prévision ≠ observation ; année sans jour (« by 2030 ») = échéance ambiguë, signalée ; date sans année = non devinée.

## Domaines

| Domaine | Contrat (ce que la question doit dire) | Connecteur |
|---|---|---|
| `WEATHER` | variable (max, min, pluie), lieu avec coordonnées et fuseau, **un jour précis**, seuil et unité, source | Open-Meteo : températures et précipitations journalières ≤ 16 jours |
| `EARTHQUAKE` | région, période, seuil de magnitude et sens, source | USGS |
| `SPORT` | équipe ou compétition, date | aucun |
| `ECONOMY` | indicateur et seuil, date, source | aucun |
| `FINANCE` | instrument, seuil de prix, date, source | aucun |
| `TECHNOLOGY` | entreprise ou produit, échéance | aucun |
| `GEOPOLITICS` | acteur, échéance | aucun |
| `SCIENCE` | sujet, échéance | aucun |
| `GENERAL` | aucun contrat spécialisé | aucun |

Un domaine sans connecteur est **compris et rapporté** (statut `NO_CONNECTOR`, avec ce qui manque), jamais collecté pour de faux. Un phénomène météo non pris en charge (ouragan, par exemple) est aussi `NO_CONNECTOR`, pas une question de température mal formée.

## Statuts d'un plan

`COLLECTABLE` (on peut chercher des données utiles), `AMBIGUOUS` (la question laisse ouvert un élément nécessaire), `OUT_OF_HORIZON` (au-delà de 16 jours de prévision), `NO_CONNECTOR`, `NOT_APPLICABLE` (marché clos). Collectable et **estimable** sont distincts : un marché sans source de résolution nommée est collecté mais `NOT_ESTIMABLE`.

## Du marché à l'estimation

```text
marché Manifold
  → analyse (critères, ambiguïtés, manques)            resolution.py
  → contrat du domaine et plan                         domain_params.py, generic_planning.py
  → choix de la source, budget, utilité                routing.py, budget.py, value.py
  → collecte et vérification (observations datées)     providers/, normalization.py
  → caractéristiques datées, disponibles à la décision context.py
  → estimation seulement si un modèle validé existe    estimation.py
```

- **Caractéristiques** : seules les informations d'un fournisseur activé et vérifié, valides pour leur genre et **déjà disponibles à la date de décision** deviennent des caractéristiques. Une prévision reste une prévision. Un séisme n'est compté que s'il est relu par la source. Un compte de zéro séisme n'est affirmé qu'après une recherche terminée.
- **Estimation** : aucun modèle n'est entraîné et validé aujourd'hui. Le résultat est donc `INSUFFICIENT_MODEL` (aucune probabilité) ; `NOT_ESTIMABLE` si le marché est ambigu ; `ESTIMATED` seulement avec un modèle validé et applicable. Une sortie non finie ou hors de ]0, 1[ est refusée.
- Le pont vers `feature_engine` ajoute des valeurs préfixées `ctx.` à une **copie** du vecteur ; `feature_engine` n'est pas modifié et n'importe pas ce paquet.

## Mesure réelle du 2026-11-02 (PostgreSQL, 1 143 marchés ouverts)

Domaines : GENERAL 626, GEOPOLITICS 252, TECHNOLOGY 156, FINANCE 47, SCIENCE 29, SPORT 22, ECONOMY 13, WEATHER 3, EARTHQUAKE 2. Plans : NO_CONNECTOR 1 091, NOT_APPLICABLE 55 (marchés clos), COLLECTABLE 2 (les deux marchés sur les séismes), AMBIGUOUS 2 (marchés d'ouragan lus par l'ancien contrat météo). Un appel USGS, 137 séismes stockés. Aucune collecte météo : aucun marché réel ouvert ne pose une question de température ou de pluie sur un jour précis. Le connecteur Open-Meteo a été vérifié par une requête réelle séparée (200, 3 prévisions datées).

## Limites

- Descriptions de marchés non lues : la synchro devrait appeler `GET /v0/market/{id}` pour les obtenir (lecture seule).
- Extraction par règles : une question formulée autrement n'est pas reconnue (elle reste `GENERAL` ou `AMBIGUOUS`, pas devinée). Les lexiques se complètent dans `research_rules.yaml`.
- Le gazetteer contient 2 régions de séisme et 6 villes. Une ville absente donne un lieu non reconnu.
- Aucune prévision quantitative : la valeur d'une prévision brute n'est pas une probabilité de résolution.
