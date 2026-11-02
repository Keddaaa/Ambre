# Évaluation

Source : `src/ambre/evaluation_engine/metrics.py` (pur), `repositories.resolved_decision_scores`, `pipeline.evaluation_for_simulation`.

## Ce qui est mesuré

Pour chaque marché binaire résolu `YES` ou `NO`, on compare deux prévisions faites **au moment de la première décision** : l'estimation du modèle et le prix du marché.

- **Brier** : moyenne de `(p − issue)²`, plus bas = mieux.
- **Log loss** : moyenne de `−log` de la probabilité donnée à l'issue réelle, avec `p` borné à `[1e-6, 1−1e-6]`.
- **Calibration** : 10 tranches égales, avec `n`, probabilité moyenne prévue et fréquence observée. Les tranches vides sont omises.
- **Différence de Brier** : modèle − marché. Négatif = le modèle fait mieux. L'intervalle à 95 % (approximation normale sur les écarts appariés) n'existe qu'à partir de 2 marchés.

## Garde-fous

- Une seule observation par marché, la première décision (ADR-007).
- Résolutions `CANCEL`, `MKT` et autres : exclues. Résolution sans `resolution_at` : exclue.
- Décision postérieure ou égale à `resolution_at` : exclue.
- Aucun échantillon : toutes les métriques valent `None`, jamais 0.
- `sufficient_sample` est vrai à partir de 30 marchés. En dessous, ne tirez aucune conclusion.

## Utilisation

```bash
uv run ambre evaluate
curl localhost:8000/evaluation
```

## Limites

- Le modèle actuel est une baseline non validée (ADR-004) : un résultat proche du prix du marché est le comportement attendu.
- Pas de comparaison par catégorie ni par horizon. Le backtesting est minimal (voir ci-dessous).
- Les marchés déjà résolus à la première synchro ne sont jamais scorés : seuls ceux suivis pendant leur vie comptent.

## Backtest chronologique

Source : `src/ambre/evaluation_engine/backtest.py` (pur), `repositories.backtest_markets`, `ambre backtest`.

Contrairement à `evaluate`, il ne dépend d'aucune simulation : il rejoue l'historique des snapshots.

- **Observation :** une par marché, au premier snapshot (`observed_at` minimal) ; le prix est la probabilité de ce snapshot.
- **Sans fuite :** pour chaque observation, la baseline `BaseRateShrinkage` est réajustée avec **uniquement** les marchés dont la résolution est connue **strictement avant** l'instant de décision. Une résolution à l'instant exact n'est pas connue.
- **Exclus :** marchés non résolus, résolutions autres que `YES`/`NO`, résolution sans `resolution_at`, premier snapshot postérieur ou égal à la résolution (ils servent toutefois à l'ajustement une fois leur résolution passée).
- **Sortie :** le même rapport que `evaluate` (Brier, log loss, calibration, différence appariée) plus un détail par observation (`folds`, dont `n_train`).

```bash
uv run ambre backtest
```

Limites : un seul prix par marché (le premier), pas de découpage ni de graine configurables, et le modèle testé est la baseline non validée (ADR-004). Un résultat proche du prix du marché est attendu ; `sufficient_sample` reste faux sous 30 marchés.

## Évaluation historique sans fuite (`ambre history-eval`)

Sources : `evaluation_engine/history.py`, `calibration.py`, `historical.py` (purs), `data_engine/history_fetch.py`, `repositories.historical_markets`. ADR-018.

**Données.** `ambre history-universe` charge des marchés binaires résolus `YES`/`NO` échantillonnés **dans le temps** (une requête de liste par ancre mensuelle, `sort=newest` et `beforeTime`, 25 marchés par ancre) ; `ambre history-fetch` lit l'historique complet de leurs paris (`GET /v0/bets`, du plus récent au plus ancien jusqu'au premier pari). Un historique qui atteint le plafond de pages est stocké comme **incomplet** et n'est jamais utilisé : ses premiers prix seraient devinés.

**Prix à un instant passé.** C'est le `probAfter` du dernier pari créé au plus tard à cet instant, ou la probabilité d'ouverture (le `probBefore` du premier pari) avant tout pari. Aucun pari postérieur n'est lu ; les paris annulés ou de rachat ne bougent pas le prix ; un pari mal formé est écarté et compté, jamais réparé. Deux paris dans la même milliseconde sont départagés par leur identifiant (arbitraire).

**Point de décision.** L'âge du marché (`horizon_days` après sa création : 1, 3, 7, 30), seulement si le marché était encore ouvert (ni résolu, ni clos). Choisir l'instant sur l'âge plutôt que sur la date de résolution évite de sélectionner les marchés d'après leur fin ; en contrepartie les marchés rapides n'ont pas de point à 30 jours, c'est pourquoi chaque horizon est évalué à part et sa population est affichée.

**Chronologie.** Pour chaque observation, tout modèle est ajusté sur les marchés **du même horizon dont la résolution est connue strictement avant** l'instant de décision, jamais sur le marché évalué. Le prix recalibré (régression logistique à deux paramètres sur le logit du prix, ancrée sur l'identité, `l2 = 1`) s'abstient sous 30 marchés résolus ; les comparaisons portent sur les marchés où les trois prévisions existent.

**Quatre prévisions** : le prix du marché (référence), la baseline AMBRE (rétrécissement vers le taux de base), le prix recalibré, et un modèle contextuel, **non évalué** : aucune caractéristique datée n'existe pour des marchés résolus avant le journal de caractéristiques, et AMBRE n'en fabrique pas (une réponse de source obtenue aujourd'hui n'est pas ce qui était su alors).

**Incertitude.** Différence appariée = Brier du modèle − Brier du marché (positive = le modèle est moins bon). Deux intervalles à 95 % : *naïf* (marchés indépendants : moyenne ± 1,96 `s/√n`, `s` écart-type d'échantillon avec `n−1`) et *par grappes* (CR1 : les résidus d'une grappe sont additionnés avant d'être élevés au carré). Une grappe regroupe les marchés dont les questions ont un recouvrement de mots de contenu d'au moins 0,6 (Jaccard, liaison simple) : des marchés presque identiques ne sont pas des preuves indépendantes. Un verdict « better » ou « worse » exige au moins 30 grappes **et** un intervalle par grappes qui exclut zéro ; sinon « inconclusive ».

### Mesure du 2026-11-02 (1 169 marchés résolus, 78 818 paris, historiques tous complets)

| Horizon | Marchés comparés | Brier marché | Brier baseline AMBRE | Différence [IC grappes] | Brier recalibré | Différence [IC grappes] |
|---|---|---|---|---|---|---|
| 1 j | 924 (855 grappes) | 0,2040 | 0,2028 | −0,0012 [−0,0026 ; +0,0001] | 0,2016 | −0,0024 [−0,0085 ; +0,0038] |
| 3 j | 833 (786) | 0,1896 | 0,1892 | −0,0005 [−0,0018 ; +0,0009] | 0,1873 | −0,0024 [−0,0078 ; +0,0030] |
| 7 j | 708 (666) | 0,1811 | 0,1810 | −0,0002 [−0,0016 ; +0,0013] | 0,1790 | −0,0021 [−0,0066 ; +0,0023] |
| 30 j | 416 (393) | 0,1518 | 0,1527 | +0,0009 [−0,0009 ; +0,0026] | 0,1487 | −0,0032 [−0,0095 ; +0,0031] |

**Lecture honnête.** Tous les verdicts sont « inconclusive » : aucun écart n'est distinguable de zéro.
- **La baseline n'est pas démontrée moins bonne que le prix.** Le backtest à 54 marchés (+0,0040, IC [+0,0024 ; +0,0056]) venait d'un petit échantillon récent (premiers prix de marchés vivants du 31 octobre au 2 novembre) ; sur 400 à 900 marchés étalés depuis 2023, l'écart est de l'ordre de ±0,001. Ce résultat ne se généralise pas, et il ne montre pas non plus que la baseline est meilleure.
- **Le prix recalibré a un Brier et un log loss un peu plus bas aux quatre horizons** (−0,002 à −0,003), mais l'intervalle contient zéro partout. Les quatre horizons réutilisent largement les mêmes marchés : ce ne sont pas quatre preuves indépendantes. Ce n'est pas une amélioration établie.
- Aucune source d'information n'est évaluée ici.

**Limites.** L'échantillon vient de listes triées par date de création (25 marchés par ancre mensuelle) : ce n'est pas un tirage aléatoire de tous les marchés. Les résolutions `CANCEL` et `MKT` sont exclues. Seuls les marchés `cpmm-1` en `MANA` passent le filtre existant. L'approximation normale est peu fiable sous ~30 grappes. Les frais, la profondeur du carnet et l'impact de prix ne sont pas modélisés : un écart de Brier n'est pas un rendement.

### Construction exacte de l'intervalle du backtest (`ambre backtest`, `ambre evaluate`)

`metrics._paired_ci` : pour chaque marché, `d_i = (p_modèle − issue)² − (p_marché − issue)²` ; `d̄` est leur moyenne, `s²` leur variance d'échantillon (diviseur `n − 1`), l'intervalle est `d̄ ± 1,96 · √(s²/n)`. Il suppose des marchés indépendants et une moyenne approximativement normale ; il n'a ni correction de dépendance, ni test de normalité, ni correction pour comparaisons multiples. L'évaluation historique ci-dessus ajoute l'intervalle par grappes.
