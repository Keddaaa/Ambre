# Audit des dix premiers paris réglés

Simulation `3ca3c581-3afb-4b7f-89e8-c109d58d5f62` (`default`, base `var/ambre.db`). Elle n'est pas le test en avant d'ADR-019. Audit en lecture seule : aucune décision, aucun plafond ni paramètre modifié.

## Conclusion

- Les dix paris respectent toutes les règles en vigueur.
- Ils ont passé parce que le modèle d'alors crée un écart mécanique, pas parce qu'Ambre savait quelque chose.
- Dix pertes ne prouvent rien : aux prix du marché, cette issue avait environ 65 % de chances (paris supposés indépendants).

## Perte : 95,42 n'est pas une perte réalisée

| Poste | Mana |
|---|---|
| Perte réalisée sur les dix paris | **-64,32** |
| Perte latente sur les positions ouvertes au 2026-11-04 15:42 UTC | -31,10 |
| Baisse totale à cet instant (1 000 → 904,58) | -95,42 |
| Baisse au dernier cycle (902,82 : latent -32,86) | -97,18 |

## Les dix décisions

Toutes prises le 2026-10-31 à 14:44 UTC, au premier cycle, par `base-rate-shrinkage-v0.1(w=0.9,n=22)`, stratégie `VALUE_HUNTER`, acteur `AMBRE`, état `BALANCED`. Réglées le 2026-11-04 à 15:42 UTC.

| Décision | Marché | Côté | Prix q | Estimé | Pénalisé | Mise | Résolution |
|---|---|---|---|---|---|---|---|
| `97da8507` | Fetterman soutient un républicain | YES | 0,108 | 0,151 | 0,129 | 5,88 | NO |
| `2429f472` | Les États-Unis attaquent Cuba | YES | 0,050 | 0,099 | 0,077 | 7,05 | NO |
| `1dd590c8` | Kevin Kiley gagne CA-6 | YES | 0,050 | 0,099 | 0,077 | 7,03 | NO |
| `642d17de` | SAVE Act adopté | YES | 0,015 | 0,068 | 0,045 | 7,65 | NO |
| `f081395f` | Interdiction des exportations de diesel | YES | 0,033 | 0,084 | 0,062 | 7,32 | NO |
| `b21cca42` | Virginie, Question 1 | NO | 0,948 | 0,907 | 0,929 | 4,77 | YES |
| `5fa9e89e` | Hilton ou Pratt gagne | YES | 0,028 | 0,079 | 0,057 | 7,39 | NO |
| `a149a19c` | Timo Kielich, 3 courses UCI | YES | 0,010 | 0,063 | 0,041 | 7,69 | NO |
| `8ef97ce4` | Texas, nouvelle carte électorale | NO | 0,980 | 0,936 | 0,958 | 5,41 | YES |
| `530fc615` | Loi ralentissant l'IA | YES | 0,044 | 0,094 | 0,071 | 4,13 | NO |

Neuf marchés sont politiques. `a149a19c` porte sur le cyclisme : « dix paris politiques » est donc inexact.

## Contrôles

| Critère | Seuil | Observé | Résultat |
|---|---|---|---|
| Liquidité | ≥ 100 | minimum 162 | OK |
| Parieurs uniques | ≥ 5 | minimum 7 | OK |
| Temps avant clôture | ≥ 1 h | minimum 8,2 h | OK |
| Âge des données | ≤ 900 s | 0 s | OK |
| Écart après impact | ≥ 0,03 | minimum 0,18 | OK |
| Kelly | sur probabilité pénalisée, × 0,25 | recalculé sur les dix | OK |
| Mise maximale | ≤ 2 % | maximum 7,69 | OK |
| Exposition | ≤ 20 % de la valeur | 197,31 pour 197,30 de plafond au 1er cycle ; `530fc615` réduite de 7,07 à 4,13 | OK |
| Survie | pas d'arrêt | `BALANCED` | OK |
| Mode | PAPER | `SIMULATED`, aucun ordre distant | OK |

## Pourquoi ces paris ont été autorisés

1. **L'écart vient de la formule.** `p = 0,9·q + 0,1·b`, avec `b = 0,542`, taux de base mesuré sur 22 marchés résolus seulement. Tout prix éloigné de ce taux produit un écart, sans aucune information sur le marché. ADR-004 classe déjà ce modèle comme non validé.
2. **Le seuil d'écart est relatif.** `minimum_edge` s'applique à l'espérance par Mana. Sur un prix de 0,010, 3 points de probabilité pénalisée deviennent +292 %. Le filtre favorise donc les paris extrêmes.
3. **La pénalité est trop faible.** L'incertitude (0,022) est inférieure à l'écart créé par la formule sur les prix extrêmes.
4. **Les marchés sont corrélés.** Plusieurs dépendent des élections du 3 novembre, mais aucune règle ne limite l'exposition par événement.

Attendu sur dix paris : 0,41 gain selon le marché, 0,89 selon le modèle. Observé : 0.

## État actuel

34 positions ouvertes pour 181,03 de coût. Le plafond vaut 20 % × 902,82 = 180,56 : il est atteint, donc aucun nouveau pari n'est possible. Il n'a pas été relevé.

## Pistes, non appliquées

Toute modification de ces règles est une décision à part : seuil d'écart absolu, limite par événement, retrait du modèle du chemin de pari. Aucune ne s'applique au test en avant, gelé par ADR-019.
