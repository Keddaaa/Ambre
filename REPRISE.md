# REPRISE — état et prochaine étape

Dernière mise à jour : 2026-11-02. Branche : `feat/backtest`. Mode : PAPER uniquement.

## Fait

- **Backtest chronologique** (`ambre backtest`, ADR-016) : sur 54 marchés, la baseline fait moins bien que le prix (Brier 0,0942 contre 0,0902, différence +0,0040).
- **Correction** de la date de clôture hors plage (année 9999 = « ne se ferme jamais »).
- **Journal de caractéristiques** (ADR-017, migration 0007) : le futur jeu d'entraînement, sans effet sur les paris.
- **Diagnostic du débit de recherche** (`ambre coverage` > `diagnostics`) : voir `docs/UNIVERSAL_RESEARCH.md`.

## Constats à garder en tête

- Le scheduler n'a jamais tourné : aucun worker actif, passes manuelles seulement.
- GDELT limite l'IP (HTTP 429) : 1 522 marchés sont différés pour cette raison, enregistrée par marché.
- Aucune preuve exploitable, aucun modèle validé. Aucune amélioration sur le prix n'est démontrée.

## Ordre de travail décidé

1. Débloquer la recherche (worker en marche, partage des sujets, second fournisseur de presse à vérifier).
2. Moteur historique : marchés résolus, contexte reconstruit à l'instant de décision, sans fuite temporelle.
3. Mesure de la contribution des sources, puis seulement un modèle.

## Décisions en attente du propriétaire

- 167 marchés ignorés faute de capital de simulation (paramètres de risque).
- Choix d'un second fournisseur de presse pour la liste blanche (ADR-010).
