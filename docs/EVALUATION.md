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
- Pas de comparaison par catégorie ni par horizon, pas de backtesting chronologique complet.
- Les marchés déjà résolus à la première synchro ne sont jamais scorés : seuls ceux suivis pendant leur vie comptent.
