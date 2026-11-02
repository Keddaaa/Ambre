# REPRISE — état et prochaine étape

Dernière mise à jour : 2026-11-02. Branche : `feat/backtest`. Mode : PAPER uniquement.

## Fait

- **Backtest chronologique** (`ambre backtest`, ADR-016) : sur 54 marchés, la baseline fait moins bien que le prix (Brier 0,0942 contre 0,0902, différence +0,0040).
- **Date de clôture hors plage** (année 9999 = « ne se ferme jamais ») : traitée comme `None`.
- **Journal de caractéristiques** (ADR-017, migration 0007) : le futur jeu d'entraînement, sans effet sur les paris.
- **Diagnostic du débit de recherche** (`ambre coverage` > `diagnostics`) : voir `docs/UNIVERSAL_RESEARCH.md`.
- **Résilience des sources de presse** (addendum d'ADR-010, migration 0008) : ingestion partagée `ambre news-ingest` et tâche planifiée `news_ingest`, adaptateur RSS/Atom générique (requêtes conditionnelles), connecteur Spaceflight News API v4, regroupement en événements. Mesure réelle ci-dessous.

## Mesure réelle de l'ingestion de presse (2026-11-02)

- 1 requête à Spaceflight News : 50 articles de 8 éditeurs, 49 événements distincts, 1 événement à plusieurs sources.
- 3 marchés reliés (tous à un même article sur un lancement de Falcon 9) ; 3 lignes de caractéristiques `news.*` journalisées ; 0 lien utilisable pour une prédiction.
- **Une des trois correspondances est fausse** : « SpaceX stock crash and close below $100 » reliée à « … close out October … » (le terme « close »). La règle de rapprochement (`topic_relevant`) est trop permissive avec les termes génériques. Elle sert aussi le chemin GDELT : à resserrer avec un test avant de s'appuyer sur ces caractéristiques.
- Aucun modèle validé, aucune amélioration sur le prix démontrée.

## Constats à garder en tête

- Le scheduler n'a tourné que par mes passes manuelles : aucun worker n'est actif. Le worker lancera désormais deux tâches, `research` et `news_ingest`.
- GDELT limite l'IP (HTTP 429). Il n'a pas été sondé de nouveau et son circuit de pause est inchangé.
- Flux BBC, Guardian, DW : adaptateur prêt, **désactivés**, conditions d'usage non confirmées (pages inaccessibles).

## Ordre de travail décidé

1. Resserrer le rapprochement des titres (termes génériques), avec un test sur le cas « close ».
2. Lancer le worker en continu pour que le journal se remplisse.
3. Moteur historique : marchés résolus, prix reconstruits depuis `GET /v0/bets` (`probBefore` / `probAfter` / `createdTime`, vérifié en lecture), sans fuite temporelle. **En pause** : schéma de lecture vérifié, rien d'écrit.
4. Mesure de la contribution des sources, puis seulement un modèle.

## Décisions en attente du propriétaire

- Confirmer l'ajout de `spaceflight_news` à la liste blanche (ADR-010) et vérifier les conditions des flux BBC / Guardian / DW avant toute activation.
- 167 marchés ignorés faute de capital de simulation (paramètres de risque).
