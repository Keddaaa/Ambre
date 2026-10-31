# Stratégies

Code : `src/ambre/strategy_engine/strategies.py`. Une stratégie transforme une estimation en **proposition avant risque** ; le moteur de risque la plafonne ensuite.

## VALUE_HUNTER (active)

Soit `q` le prix du marché, `p` l'estimation et `u` son incertitude.

- `p > q` → côté YES, probabilité conservatrice `p_cons = clamp(p − u)`. Pas de pari si `p_cons ≤ q`.
- `p < q` → côté NO, `p_cons = clamp(p + u)`. Pas de pari si `p_cons ≥ q`.
- Mise = `kelly_fraction × Kelly(p_cons, q) × valeur du portefeuille` (fraction 0,25).
- `q` ≤ 0 ou ≥ 1 : pas de proposition (Kelly indéfini). `p_cons` est borné à [0,001 ; 0,999].
- Chaque proposition liste ses raisons et ses conditions d'invalidation.

Le Kelly intégral n'est jamais utilisé.

## NO_TRADE

Baseline explicite qui ne propose jamais rien.

## Après la stratégie

Risque : mise ≤ 2 % de la valeur, exposition totale ≤ 20 %, edge ≥ 3 % **après impact CPMM**, mise minimale 1 Mana, frais inclus. Survie : multiplicateur de mise (DEFENSIVE = ×0,5) ou blocage.

## Limites

Une seule stratégie existe. Les opportunités sont **théoriques** : elles dépendent d'une baseline non validée (`PREDICTION_ENGINE.md`).
