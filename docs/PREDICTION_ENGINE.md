# Moteur de prédiction

Code : `src/ambre/prediction_engine/baselines.py`, `src/ambre/feature_engine/features.py`. Modules purs.

## Features

`extract_features` n'utilise que ce qui était disponible à `observed_at` (pas de fuite du futur). Une valeur absente reste `None`.

## Modèles

| Modèle | Comportement |
|---|---|
| `MarketPriceBaseline` | Renvoie le prix du marché. Par construction, il ne trouve jamais d'edge. Sert de référence. |
| `BaseRateShrinkage` (utilisé par le pipeline) | `p = w·q + (1−w)·taux_de_base`, avec `w = 0,9`. |

Le taux de base est la moyenne a posteriori beta-binomiale `(yes+1)/(n+2)` sur les marchés résolus YES/NO ; CANCEL, MKT et autres sont ignorés. L'incertitude combine l'écart-type du taux de base et un plancher de 0,02.

## Statut : non validé

`BaseRateShrinkage` est un estimateur **non validé** (ADR-004). Aucune mesure hors échantillon (Brier, log loss, calibration) n'existe en 0.1. Les opportunités qu'il produit sont théoriques et n'indiquent aucune rentabilité.

## Prochaines étapes

Métriques Brier et log loss sur les marchés résolus, protocole de validation reproductible, backtesting chronologique (voir `ROADMAP.md`). Une réponse de LLM ne sera jamais traitée comme une probabilité vraie.
