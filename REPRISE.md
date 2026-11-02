# REPRISE — état et prochaine étape

Dernière mise à jour : 2026-11-02. Branche : `feat/backtest`. Mode : PAPER uniquement.

## Fait

- **Backtest chronologique** (`ambre backtest`, ADR-016) sur 54 marchés récents : baseline moins bonne que le prix (+0,0040). **Ne se généralise pas** (voir ci-dessous).
- **Journal de caractéristiques** (ADR-017, migration 0007) : le futur jeu d'entraînement contextuel, sans effet sur les paris.
- **Diagnostic du débit de recherche** (`ambre coverage` > `diagnostics`).
- **Ingestion de presse partagée** (addendum d'ADR-010, migration 0008) : tâche `news_ingest`, adaptateur RSS/Atom, Spaceflight News API v4 (activée), flux BBC/Guardian/DW **désactivés** (conditions non confirmées). Rapprochement des titres resserré (`GENERIC_TERMS`).
- **Moteur historique sans fuite** (ADR-018, migration 0009) : `ambre history-universe`, `history-fetch`, `history-eval`.

## Mesure historique (2026-11-02)

5 663 marchés résolus échantillonnés depuis 2023 (ancres hebdomadaires), 454 951 paris, historiques complets. Quatre horizons (1, 3, 7, 30 jours après la création), 2 419 à 4 850 marchés comparés par horizon. Verdicts corrigés pour 8 comparaisons.

- **Baseline AMBRE contre prix du marché :** écart de l'ordre de 0,0001 de Brier, **inconclusif partout**. Le +0,004 du backtest à 54 marchés ne se retrouve pas.
- **Biais constant du prix :** le prix moyen dépasse le taux réel de `YES` de 5 à 8 points, chaque année 2023 à 2026, à tous les horizons.
- **Prix recalibré :** « better » à 3 jours seulement (−0,0030 de Brier, intervalle corrigé [−0,0056 ; −0,0005], signe stable sur les quatre années). Inconclusif et instable à 1, 7 et 30 jours (2023 est moins bon que le prix à 7 et 30 jours).
- **Ce n'est pas un rendement.** Frais, profondeur et impact du prix ne sont pas modélisés ; l'échantillon ne contient que des marchés résolus ; une partie du biais peut venir du mélange de questions. Rien ne démontre qu'on gagnerait de l'argent.
- **Modèle contextuel :** `NOT_EVALUATED` (aucune caractéristique datée pour des marchés déjà résolus).
- Détail, méthode, limites : `docs/EVALUATION.md`.

## Test en avant en cours (ADR-019, lancé le 2026-11-02)

Hypothèse pré-enregistrée : à 2-4 jours d'âge, le prix surestime `P(YES)` ; `P(YES) = sigmoid(−0,3181 + 0,9372·logit q)`. Paramètres, fenêtre, frais (2 %) et critère de succès sont figés dans `docs/DECISIONS.md` (ADR-019) et `configs/forward_test.yaml` : **ne pas les modifier pendant le test**.

- **Instance :** worker séparé, base `var/forward_test.db`, PAPER seulement, recherche et presse coupées, `MAX_MARKETS_PER_CYCLE=5000` (sans cela, le plafond de 200 marchés par cycle ne couvre pas la fenêtre).
  ```
  MAX_MARKETS_PER_CYCLE=5000 AMBRE_CONFIG=configs/forward_test.yaml DATABASE_URL=sqlite:///./var/forward_test.db ambre worker --interval 300
  ```
- **Lire l'état :** même variables d'environnement, puis `ambre forward-report`. Le verdict et tous les chiffres qui le laissent deviner restent masqués (`INSUFFICIENT_SAMPLE`) tant qu'il n'y a pas **300 marchés résolus dans 30 grappes**.
- **Premier cycle :** 2 011 marchés évalués, 29 avec une décision dans la fenêtre, 10 mises `NO` simulées, 0 mise hors fenêtre. L'exposition totale approche déjà son plafond de 20 % : peu de paris actifs en même temps, mais le critère principal porte sur les **décisions**, pas sur les mises.
- **Durée :** inconnue. Ordre de grandeur issu des chiffres mesurés (environ 15 marchés éligibles entrent dans la fenêtre par jour) : au moins trois semaines rien que pour atteindre 300 marchés, plus le temps de résolution. À mesurer, pas à promettre.
- **Si le worker s'arrête**, les marchés qui passent dans la fenêtre pendant ce temps sont manqués.

## Constats à garder en tête

- Le scheduler n'a tourné que par mes passes manuelles : aucun worker actif. Le worker lancera `research` et `news_ingest`.
- GDELT limite l'IP (HTTP 429), circuit de pause inchangé.
- Presse spatiale seulement : environ 44 marchés ouverts sur 1 608 concernés.

## Ordre de travail proposé

1. Lancer le worker en continu pour remplir le journal de caractéristiques (décision du propriétaire : processus long avec lectures réseau).
2. Répéter `history-universe` + `history-fetch` périodiquement pour étoffer l'échantillon ; envisager un tirage plus aléatoire que « 25 par ancre ».
3. Quand le journal contiendra assez de marchés résolus avec caractéristiques datées : moteur d'expériences avec/sans source (même protocole chronologique, mêmes intervalles).
4. ~~Essai en avant du biais de prix~~ : **lancé** (voir ci-dessus). Ne rien conclure avant le seuil de 300 marchés résolus.

## Décisions en attente du propriétaire

- Confirmer l'ajout de `spaceflight_news` à la liste blanche (ADR-010) ; vérifier les conditions BBC / Guardian / DW avant toute activation.
- 167 marchés ignorés faute de capital de simulation (paramètres de risque).
