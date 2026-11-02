# Limites des API

Vérification du **2026-11-02**. Source : documentation officielle lue ce jour, ou comportement observé (indiqué). `UNKNOWN` = non vérifiable. Un budget interne d'AMBRE est toujours **plus bas** que la limite du fournisseur ; il n'est pas une limite du fournisseur.

| Fournisseur | Limite connue | Origine | Autorisation | Budget interne proposé (à valider en phase B) |
|---|---|---|---|---|
| `gdelt` | 1 requête / 5 s | message du serveur (HTTP 429 observé) ; documentation : UNKNOWN | aucune | ≤ 1 requête / 10 s, ≤ 2 000 / jour |
| `open_meteo` | 600/min, 5 000/h, 10 000/jour, 300 000/mois | page des conditions | aucune ; **non commercial** | ≤ 500/jour (les prévisions changent au plus toutes les heures) |
| `nasa_eonet` | UNKNOWN | — | aucune observée | ≤ 1 requête / 5 min |
| `usgs_earthquakes` | 20 000 événements / requête ; débit UNKNOWN | documentation | aucune observée | utiliser un flux GeoJSON, ≤ 1 requête / min |
| `hacker_news` | « aucune limite » | documentation | aucune | ≤ 1 requête / s |
| `github` | 60/h par IP sans jeton ; 5 000/h avec jeton ; secondaires : 100 concurrentes, 900 points/min | documentation | jeton optionnel | ≤ 30/h sans jeton, lecture de `x-ratelimit-remaining` |
| `bluesky` | UNKNOWN | page non lisible | aucune pour `getAuthorFeed` | ≤ 1 requête / 5 s tant que UNKNOWN |
| `manifold` | 500 requêtes/min par IP | documentation | aucune pour la lecture | déjà borné par le client existant |

Règles communes (phase B) :

- Toute réponse `429` ou `Retry-After` suspend le fournisseur jusqu'à l'échéance, puis reprend avec un recul exponentiel. Aucune insistance, aucun contournement par proxy ou par rotation d'adresse.
- Un fournisseur dont la limite est `UNKNOWN` reçoit le budget le plus bas de la table, jamais « illimité ».
- Un budget n'est jamais dépensé pour une recherche secondaire s'il laisse moins que la réserve d'une recherche prioritaire.
- Aucun appel payant : aucun fournisseur retenu n'en exige.
