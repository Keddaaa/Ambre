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
