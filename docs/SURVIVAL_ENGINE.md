# Moteur de survie

Code : `src/ambre/survival_engine/survival.py` (pur). Il convertit la santé du portefeuille en état opérationnel.

## Les règles

Appliquées dans l'ordre ; la première qui correspond l'emporte.

| # | Condition | État |
|---|---|---|
| 1 | Arrêt manuel | `HALTED` |
| 2 | Exposition inconnue (erreur base, prix manquant) | `OBSERVING` |
| 3 | Valorisation non finie | `HALTED` |
| 4 | Valeur ≤ 50 % du capital initial | `HALTED`, simulation déclarée `DEAD` |
| 5 | Données absentes, invalides ou plus vieilles que 900 s | `OBSERVING` |
| 6 | Drawdown ≥ 30 % | `CRITICAL` |
| 7 | Drawdown ≥ 15 % | `DEFENSIVE` (mises ×0,5) |
| 8 | Sinon | `BALANCED` |

`HALTED`, `OBSERVING` et `CRITICAL` interdisent toute nouvelle position. La règle 2 passe avant la règle 4 : une valorisation incomplète ne peut jamais tuer une simulation.

## États atteignables en 0.1

`BALANCED`, `DEFENSIVE`, `OBSERVING`, `CRITICAL`, `HALTED`.

## Absent par rapport à la spec

- exploration et spécialisation stratégiques ;
- mode de récupération après un drawdown ;
- fonction de récompense long terme ;
- adaptation des paramètres à partir de l'historique.

Ces éléments ne sont pas simulés : ils sont listés dans `ROADMAP.md`.
