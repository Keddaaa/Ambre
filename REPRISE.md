# REPRISE — état et prochaine étape

Dernière mise à jour : 2026-11-02. Branche : `feat/backtest`. Mode : PAPER uniquement.

## Fait

- **Backtest chronologique** (`ambre backtest`, ADR-016) sur 54 marchés récents : baseline moins bonne que le prix (+0,0040). **Ne se généralise pas** (voir ci-dessous).
- **Journal de caractéristiques** (ADR-017, migration 0007) : le futur jeu d'entraînement contextuel, sans effet sur les paris.
- **Diagnostic du débit de recherche** (`ambre coverage` > `diagnostics`).
- **Ingestion de presse partagée** (addendum d'ADR-010, migration 0008) : tâche `news_ingest`, adaptateur RSS/Atom, Spaceflight News API v4 (activée), flux BBC/Guardian/DW **désactivés** (conditions non confirmées). Rapprochement des titres resserré (`GENERIC_TERMS`).
- **Moteur historique sans fuite** (ADR-018, migration 0009) : `ambre history-universe`, `history-fetch`, `history-eval`.

## Mesure historique (2026-11-02)

1 169 marchés résolus échantillonnés depuis 2023, 78 818 paris, historiques complets. Quatre horizons (1, 3, 7, 30 jours après la création), 416 à 924 marchés comparés par horizon.

- **Baseline AMBRE contre prix du marché :** écart de ±0,001 de Brier, **inconclusif partout**. L'écart de +0,004 du backtest à 54 marchés ne se retrouve pas : il venait d'un petit échantillon récent.
- **Prix recalibré (logit, ancré sur l'identité) :** Brier et log loss un peu plus bas aux quatre horizons (−0,002 à −0,003), mais intervalles par grappes contenant zéro. **Pas une amélioration établie** ; les horizons réutilisent les mêmes marchés.
- **Modèle contextuel :** `NOT_EVALUATED` (aucune caractéristique datée pour des marchés déjà résolus ; rien de fabriqué).
- Détail, méthode, limites et construction exacte des intervalles : `docs/EVALUATION.md`.

## Constats à garder en tête

- Le scheduler n'a tourné que par mes passes manuelles : aucun worker actif. Le worker lancera `research` et `news_ingest`.
- GDELT limite l'IP (HTTP 429), circuit de pause inchangé.
- Presse spatiale seulement : environ 44 marchés ouverts sur 1 608 concernés.

## Ordre de travail proposé

1. Lancer le worker en continu pour remplir le journal de caractéristiques (décision du propriétaire : processus long avec lectures réseau).
2. Répéter `history-universe` + `history-fetch` périodiquement pour étoffer l'échantillon ; envisager un tirage plus aléatoire que « 25 par ancre ».
3. Quand le journal contiendra assez de marchés résolus avec caractéristiques datées : moteur d'expériences avec/sans source (même protocole chronologique, mêmes intervalles).
4. Décider si le prix recalibré mérite un essai en simulation PAPER : à ce jour rien ne le justifie statistiquement.

## Décisions en attente du propriétaire

- Confirmer l'ajout de `spaceflight_news` à la liste blanche (ADR-010) ; vérifier les conditions BBC / Guardian / DW avant toute activation.
- 167 marchés ignorés faute de capital de simulation (paramètres de risque).
