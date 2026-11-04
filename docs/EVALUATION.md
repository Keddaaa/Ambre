# Évaluation

Source : `src/ambre/evaluation_engine/metrics.py` (pur), `repositories.resolved_decision_scores`, `pipeline.evaluation_for_simulation`.

## Ce qui est mesuré

Pour chaque marché binaire résolu `YES` ou `NO`, on compare deux prévisions faites **au moment de la première décision** : l'estimation du modèle et le prix du marché.

- **Brier** : moyenne de `(p − issue)²`, plus bas = mieux.
- **Log loss** : moyenne de `−log` de la probabilité donnée à l'issue réelle, avec `p` borné à `[1e-6, 1−1e-6]`.
- **Calibration** : 10 tranches égales, avec `n`, probabilité moyenne prévue et fréquence observée. Les tranches vides sont omises.
- **Différence de Brier** : modèle − marché. Négatif = le modèle fait mieux. L'intervalle à 95 % (approximation normale sur les écarts appariés) n'existe qu'à partir de 2 marchés.

## Garde-fous

- Une seule observation par marché, la première décision (ADR-007).
- Résolutions `CANCEL`, `MKT` et autres : exclues. Résolution sans `resolution_at` : exclue.
- Décision postérieure ou égale à `resolution_at` : exclue.
- Aucun échantillon : toutes les métriques valent `None`, jamais 0.
- `sufficient_sample` est vrai à partir de 30 marchés. En dessous, ne tirez aucune conclusion.

## Utilisation

```bash
uv run ambre evaluate
curl localhost:8000/evaluation
```

## Limites

- Le modèle actuel est une baseline non validée (ADR-004) : un résultat proche du prix du marché est le comportement attendu.
- Pas de comparaison par catégorie ni par horizon. Le backtesting est minimal (voir ci-dessous).
- Les marchés déjà résolus à la première synchro ne sont jamais scorés : seuls ceux suivis pendant leur vie comptent.

## Backtest chronologique

Source : `src/ambre/evaluation_engine/backtest.py` (pur), `repositories.backtest_markets`, `ambre backtest`.

Contrairement à `evaluate`, il ne dépend d'aucune simulation : il rejoue l'historique des snapshots.

- **Observation :** une par marché, au premier snapshot (`observed_at` minimal) ; le prix est la probabilité de ce snapshot.
- **Sans fuite :** pour chaque observation, la baseline `BaseRateShrinkage` est réajustée avec **uniquement** les marchés dont la résolution est connue **strictement avant** l'instant de décision. Une résolution à l'instant exact n'est pas connue.
- **Exclus :** marchés non résolus, résolutions autres que `YES`/`NO`, résolution sans `resolution_at`, premier snapshot postérieur ou égal à la résolution (ils servent toutefois à l'ajustement une fois leur résolution passée).
- **Sortie :** le même rapport que `evaluate` (Brier, log loss, calibration, différence appariée) plus un détail par observation (`folds`, dont `n_train`).

```bash
uv run ambre backtest
```

Limites : un seul prix par marché (le premier), pas de découpage ni de graine configurables, et le modèle testé est la baseline non validée (ADR-004). Un résultat proche du prix du marché est attendu ; `sufficient_sample` reste faux sous 30 marchés.

## Évaluation historique sans fuite (`ambre history-eval`)

Sources : `evaluation_engine/history.py`, `calibration.py`, `historical.py` (purs), `data_engine/history_fetch.py`, `repositories.historical_markets`. ADR-018.

**Données.** `ambre history-universe` charge des marchés binaires résolus `YES`/`NO` échantillonnés **dans le temps** (une requête de liste par ancre mensuelle, `sort=newest` et `beforeTime`, 25 marchés par ancre) ; `ambre history-fetch` lit l'historique complet de leurs paris (`GET /v0/bets`, du plus récent au plus ancien jusqu'au premier pari). Un historique qui atteint le plafond de pages est stocké comme **incomplet** et n'est jamais utilisé : ses premiers prix seraient devinés.

**Prix à un instant passé.** C'est le `probAfter` du dernier pari créé au plus tard à cet instant, ou la probabilité d'ouverture (le `probBefore` du premier pari) avant tout pari. Aucun pari postérieur n'est lu ; les paris annulés ou de rachat ne bougent pas le prix ; un pari mal formé est écarté et compté, jamais réparé. Deux paris dans la même milliseconde sont départagés par leur identifiant (arbitraire).

**Point de décision.** L'âge du marché (`horizon_days` après sa création : 1, 3, 7, 30), seulement si le marché était encore ouvert (ni résolu, ni clos). Choisir l'instant sur l'âge plutôt que sur la date de résolution évite de sélectionner les marchés d'après leur fin ; en contrepartie les marchés rapides n'ont pas de point à 30 jours, c'est pourquoi chaque horizon est évalué à part et sa population est affichée.

**Chronologie.** Pour chaque observation, tout modèle est ajusté sur les marchés **du même horizon dont la résolution est connue strictement avant** l'instant de décision, jamais sur le marché évalué. Le prix recalibré (régression logistique à deux paramètres sur le logit du prix, ancrée sur l'identité, `l2 = 1`) s'abstient sous 30 marchés résolus ; les comparaisons portent sur les marchés où les trois prévisions existent.

**Quatre prévisions** : le prix du marché (référence), la baseline AMBRE (rétrécissement vers le taux de base), le prix recalibré, et un modèle contextuel, **non évalué** : aucune caractéristique datée n'existe pour des marchés résolus avant le journal de caractéristiques, et AMBRE n'en fabrique pas (une réponse de source obtenue aujourd'hui n'est pas ce qui était su alors).

**Incertitude.** Différence appariée = Brier du modèle − Brier du marché (positive = le modèle est moins bon). Deux intervalles à 95 % : *naïf* (marchés indépendants : moyenne ± 1,96 `s/√n`, `s` écart-type d'échantillon avec `n−1`) et *par grappes* (CR1 : les résidus d'une grappe sont additionnés avant d'être élevés au carré). Une grappe regroupe les marchés dont les questions ont un recouvrement de mots de contenu d'au moins 0,6 (Jaccard, liaison simple) : des marchés presque identiques ne sont pas des preuves indépendantes. Un verdict « better » ou « worse » exige au moins 30 grappes **et** un intervalle par grappes qui exclut zéro ; sinon « inconclusive ».

### Mesure du 2026-11-02 (5 663 marchés résolus chargés, 454 951 paris, historiques tous complets)

Le lot est échantillonné par ancres hebdomadaires de création depuis 2023 (25 marchés par ancre). Quatre horizons (1, 3, 7 et 30 jours après la création) ; 2 419 à 4 850 marchés comparés par horizon. Intervalles par grappes ; la dernière colonne est élargie pour les **8 comparaisons** du rapport (2 modèles × 4 horizons, correction de Bonferroni), et c'est elle qui décide du verdict.

| Horizon | Marchés | Brier prix | Baseline AMBRE : écart | Prix recalibré : écart | IC par grappes | IC corrigé (8) | Verdict |
|---|---|---|---|---|---|---|---|
| 1 j | 4 850 | 0,1981 | −0,0001 | −0,0013 | [−0,0027 ; +0,0001] | [−0,0033 ; +0,0006] | inconclusif |
| 3 j | 4 337 | 0,1916 | −0,0003 | **−0,0030** | [−0,0049 ; −0,0012] | **[−0,0056 ; −0,0005]** | **better** |
| 7 j | 3 623 | 0,1834 | −0,0001 | −0,0007 | [−0,0023 ; +0,0010] | [−0,0030 ; +0,0017] | inconclusif |
| 30 j | 2 419 | 0,1650 | 0,0000 | −0,0019 | [−0,0043 ; +0,0005] | [−0,0053 ; +0,0015] | inconclusif |

La baseline AMBRE est « inconclusif » à chaque horizon (écart de l'ordre de 0,0001).

**Un biais du prix, constant.** Le prix moyen dépasse le taux réel de `YES` de 5 à 8 points, chaque année et à chaque horizon (par exemple à 3 jours : 2023 0,47 contre 0,39 ; 2024 0,45 contre 0,40 ; 2025 0,44 contre 0,39 ; 2026 0,47 contre 0,42). La calibration par tranches va dans le même sens : à 3 jours, un prix de 0,65 se réalise 54 % du temps, 0,75 à 64 %, 0,35 à 26 %. L'ajustement logistique sur tout l'échantillon donne `a ≈ −0,3` et `b ≈ 0,94` : un décalage vers le bas bien plus qu'une confiance excessive.

**Ce que le prix recalibré apporte, et ce qu'il n'apporte pas.**
- À 3 jours, il abaisse le Brier de 0,0030 (environ 1,6 %), et l'intervalle corrigé exclut zéro. Cet effet a le même signe dans les quatre années (−0,0041, −0,0021, −0,0021, −0,0044).
- Aux autres horizons, il n'est pas établi, et il n'est pas stable : à 7 et 30 jours, 2023 est **moins bon** que le prix (+0,0077 et +0,0047) alors que 2024 et 2025 sont meilleurs. Une propriété stable du marché ne changerait pas de signe ainsi.
- Les quatre horizons réutilisent en grande partie les mêmes marchés : ce ne sont pas quatre confirmations indépendantes.

**Limites de cette mesure.**
- L'échantillon ne contient que des marchés **déjà résolus** `YES` ou `NO` (les `CANCEL` et `MKT` sont exclus) : un marché encore ouvert n'y figure pas. Cette sélection peut pousser les taux de `YES` vers le bas pour les ancres récentes. Elle ne peut pas expliquer l'écart de 2023, où presque tout est résolu, mais elle n'est pas écartée pour autant.
- Une partie du biais peut venir du mélange de questions de Manifold (beaucoup de « X arrivera-t-il avant telle date ? », qui finissent à `NO`), pas d'une erreur de prix exploitable.
- Un écart de Brier n'est pas un rendement : les frais, la profondeur de marché et l'impact du prix ne sont pas modélisés. **Rien ici ne démontre qu'un parieur gagnerait de l'argent.**
- Les ancres hebdomadaires donnent 25 marchés par semaine de création, pas un tirage aléatoire de tous les marchés.
- L'approximation normale est celle des grappes ; la correction de Bonferroni est prudente mais suppose un nombre de comparaisons connu à l'avance (ici 8).
- Aucune source d'information n'est évaluée.

**Hypothèse à tester en avant, pas à croire.** Si le biais de 5 points persiste, il se verra aussi sur des marchés encore ouverts. Le test honnête est un essai chronologique en simulation PAPER sur des marchés non encore résolus, avec des frais explicites, et non une lecture de cette table.

### Construction exacte de l'intervalle du backtest (`ambre backtest`, `ambre evaluate`)

`metrics._paired_ci` : pour chaque marché, `d_i = (p_modèle − issue)² − (p_marché − issue)²` ; `d̄` est leur moyenne, `s²` leur variance d'échantillon (diviseur `n − 1`), l'intervalle est `d̄ ± 1,96 · √(s²/n)`. Il suppose des marchés indépendants et une moyenne approximativement normale ; il n'a ni correction de dépendance, ni test de normalité, ni correction pour comparaisons multiples. L'évaluation historique ci-dessus ajoute l'intervalle par grappes.

## Statistiques de paris

Source : `src/ambre/evaluation_engine/betting.py` (pur), `pipeline.betting_stats_for_simulation`, `GET /stats`, page `/stats` du dashboard.

Un pari est une position réellement ouverte. Une décision `REJECTED` porte une mise proposée mais jamais engagée : elle ne compte que dans l'entonnoir des décisions.

- Gagné ou perdu se juge sur l'argent (`payout` contre `cost`), ce qui couvre les résolutions `MKT` partielles.
- Les remboursements (`CANCEL`) et les paris à l'équilibre sortent du taux de réussite. Les remboursements sortent aussi du ROI.
- L'intervalle du taux de réussite est un intervalle de Wilson à 95 %, valable à 0 % ou 100 % sur un petit échantillon.
- Une valeur non calculable reste `null` : pas de taux sans pari décidé, pas de P&L latent si un prix manque.

Limites : pas de répartition par thème, car `group_slugs` est vide sur les marchés. En dessous de 30 paris décidés, aucune conclusion n'est permise.

## Justifications d'Ambre

Source : `apps/dashboard/lib/justification.ts` (pur). Le Journal et la page Statistiques affichent, sous chaque décision, une explication à la première personne et au féminin.

Le texte est reconstruit uniquement à partir des champs journalisés à la décision. Aucun modèle de langue ne le rédige, et une raison non traduite reste affichée telle quelle.

Chaque décision cite ses sources (`market_url`, `research_sources` dans `GET /decisions` et `GET /markets/{id}`) :

- La page du marché Manifold, seule source de l'estimation.
- Au plus 5 recherches liées au marché. Un lien créé après la décision n'est jamais cité, pour ne pas réécrire ce qu'Ambre savait.
- Ces recherches sont affichées comme lues, sans effet sur l'estimation : `ambre.intelligence` n'alimente pas le modèle (ADR-010).
- Une URL issue d'un flux n'est rendue cliquable que si elle est en `http` ou `https`.
