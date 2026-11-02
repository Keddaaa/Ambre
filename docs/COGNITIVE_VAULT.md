# Cognitive Vault (v0.1)

Décision : ADR-012. Module : `src/ambre/cognitive/`. Tables : migration `0004`.

Le Cognitive Vault est la mémoire structurée d'Ambre : des notes reliées, versionnées et sourcées, et un registre de règles qui reste **inerte** tant qu'un humain ne l'active pas. Il ne décide jamais d'une mise et n'a aucun chemin vers le moteur de risque, de survie, d'exécution ou le portefeuille.

## Ce qui existe

| Fonction | Où | Remarque |
|---|---|---|
| 13 types de notes, statut, niveau de preuve, validité, contexte | `models.py` | confiance et preuve valent `None` / `NONE` tant qu'elles ne sont pas mesurées |
| Stockage, versions append-only, verrou optimiste | `vault.py`, `orm.py` | une mise à jour concurrente périmée est refusée (`VersionConflict`) |
| Relations avec provenance (11 types) | `vault.py` | une relation factuelle exige une source ; la similarité seule est refusée ; une relation retirée est conservée |
| Doublons exacts, contradictions, support indépendant | `vault.py` | des copies d'une même substance comptent pour un seul support |
| Recherche | `vault.py` | tous les termes, titre pondéré, filtres type, statut, domaine ; `%` et `_` sont du texte |
| Export Markdown | `markdown.py`, `ambre vault-export --dir` | frontmatter YAML contrôlé, liens `[[titre]]`, lecture seule |
| Registre de règles et cycle de vie | `rules.py`, `lifecycle.py` | DRAFT → PROPOSED → TESTING → VALIDATED → ACTIVE → DEPRECATED, REJECTED, ROLLED_BACK |
| Contraintes immuables et adaptatives | `constraints.py` | voir ci-dessous |
| Audit | `cognitive_events` | création, mise à jour, refus, transitions : qui, quoi, pourquoi |

## Contraintes

- **Niveau A (immuable, dans le code).** Liste fermée de 8 actions (`prioritize_source`, `deprioritize_source`, `prefer_official_sources`, `reduce_api_calls`, `tighten_limit`, `raise_confidence_threshold`, `suspend_strategy`, `add_observation_period`). Aucune ne peut miser ou modifier le mode, les secrets ou l'arrêt d'urgence. Les noms protégés (`execution_mode`, `enable_manifold_orders`, `api_key`, `secret`, `password`, `halted`, `resume`, `critical_drawdown`, `failure_value_fraction`) sont refusés dans toute action. Une règle ne peut pas renoncer aux limites dures.
- **Niveau B (adaptatif).** Une règle ne peut que resserrer : abaisser `max_position_fraction`, `max_total_exposure_fraction`, `kelly_fraction`, relever `minimum_edge`, `min_stake`, réduire les appels d'un fournisseur (facteur dans ]0, 1]). La comparaison se fait contre le `RiskConfig` en vigueur au moment de la proposition **et de l'activation**.
- **Niveau C (préférences).** Priorité de sources, sources officielles : elles ne touchent aucun réglage de sécurité.

## Règles : de la proposition à l'activation

1. `propose_rule` valide la structure (contraintes, notes sources existantes, au moins une). Une proposition invalide est conservée en `REJECTED` avec ses raisons. Une proposition valide devient `PROPOSED` (version 2) et **n'est pas active**.
2. `TESTING` : Ambre, Claude ou le propriétaire peuvent l'ouvrir.
3. `VALIDATED` : exige une preuve mesurée sur données mises de côté (au moins 30 échantillons, méthode, score fini) **et** l'acteur `USER`.
4. `ACTIVE` : exige `USER`, revérifie la règle contre les limites actuelles et refuse un conflit avec une règle déjà active.
5. `ROLLED_BACK` / `DEPRECATED` : la règle cesse d'être active, tout l'historique reste.

Pourquoi seul le propriétaire promeut : sans moteur d'expériences, la preuve est fournie par l'appelant et n'est pas vérifiable. Une preuve qu'Ambre écrit lui-même ne prouve rien.

## Ce qui n'existe pas encore

Raccourcis déclaratifs, expériences isolées, promotion automatique, rollback par expérience, méta-apprentissage, mémoire épisodique et sémantique distinctes, graphe de traversée, intégration avec l'Information Planner, le Prediction Engine, le Strategy Engine et le Survival Engine, pages du dashboard. **Aucun moteur ne lit encore `active_rules()`** : une règle ACTIVE ne change donc aujourd'hui aucun comportement. Ambre ne s'auto-améliore pas à ce stade : ce module en pose les fondations vérifiables.

## Commandes

```bash
uv run ambre vault-export --dir ./vault-md   # écrit une note Markdown par connaissance
```
