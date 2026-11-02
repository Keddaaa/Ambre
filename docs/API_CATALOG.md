# Catalogue des API (Global Intelligence Network)

Vérification du **2026-11-02**. Chaque fiche distingue ce que la documentation officielle affirme, ce qu'une requête réelle a montré, et ce qui reste `UNKNOWN`. Aucun quota n'est inventé. Les limites et licences sont détaillées dans `API_LIMITS.md` et `DATA_LICENSES.md`.

Méthode : pour chaque fournisseur, lecture de la documentation officielle (WebFetch), puis une requête GET publique sans clé, sans compte, avec un `User-Agent` qui identifie le projet. Aucune requête d'écriture. Un résultat « observé » prouve seulement que l'accès anonyme a fonctionné ce jour-là, pas qu'il le restera.

Statuts : `VERIFIED_FREE`, `VERIFIED_LIMITED`, `REQUIRES_REGISTRATION`, `REQUIRES_APPROVAL`, `PAID_ONLY`, `UNAVAILABLE`, `UNVERIFIED`.

**Liste blanche initiale (ADR-006, ADR-010).** Seuls les 8 fournisseurs ci-dessous peuvent être activés. Les autres noms du catalogue du propriétaire restent `UNVERIFIED` et désactivés tant qu'une fiche n'a pas été complétée.

## Fournisseurs vérifiés

### `gdelt` — GDELT DOC 2.0 (actualités mondiales)

| Champ | Valeur |
|---|---|
| Domaine | actualités internationales, géopolitique |
| URL officielle | https://www.gdeltproject.org |
| Documentation | https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/ |
| Endpoint pertinent | `https://api.gdeltproject.org/api/v2/doc/doc` (`query`, `mode`, `format`, `timespan` ≥ 15 min, `maxrecords` ≤ 250, `sort`, `STARTDATETIME`, `ENDDATETIME`) |
| Authentification | aucune observée (requêtes anonymes acceptées) |
| Accès gratuit | oui (voir licence) |
| Quota gratuit | **UNKNOWN** dans la documentation ; le serveur répond « limit requests to one every 5 seconds » (HTTP 429 observé à la première requête) |
| Fréquence autorisée | 1 requête toutes les 5 s (message du serveur, pas la documentation) |
| Fraîcheur | UNKNOWN (fréquence d'actualisation non documentée sur la page lue) ; `seendate` observé d'environ 1 semaine pour une requête sur 1 semaine |
| Historique | fenêtre glissante de 3 mois pour DOC 2.0 ; recherche possible depuis le 2017-01-01 selon la documentation |
| Couverture | mondiale ; champs `sourcecountry`, `language` |
| Langues | multilingue (observé : chinois, russe) |
| Formats | HTML (défaut), CSV, RSS, JSON, JSONP, JSONFeed |
| Licence | usage gratuit, commercial inclus, redistribution permise, **citation de GDELT obligatoire** ; licence nommée : UNKNOWN |
| Restrictions commerciales | aucune déclarée |
| Restrictions de stockage | aucune déclarée (redistribution autorisée) |
| Restrictions d'entraînement IA | non mentionnées : UNKNOWN |
| Fiabilité et qualité | moyenne : agrégateur automatique, titres bruts, doublons et reprises fréquents |
| Coût estimé | 0 |
| Statut | **VERIFIED_LIMITED** |

Observé : 3 requêtes, 1 réponse `429`, 2 réponses `200` avec le corps `{}` (requêtes à 1 jour), 1 réponse `200` avec des articles (`ArtList`, 1 semaine). Champs d'un article : `url`, `url_mobile`, `title`, `seendate` (`YYYYMMDDTHHMMSSZ`), `socialimage`, `domain`, `language`, `sourcecountry`. **`seendate` est l'heure à laquelle GDELT a vu l'article, pas la date de publication** : `published_at` reste `None`, `available_at` = `seendate`.

Limites : un corps `{}` ne signifie ni « aucun article » ni erreur : il doit être traité comme un résultat vide non concluant. Un 429 pose un temps d'attente avant toute reprise.

### `open_meteo` — Open-Meteo (météo)

| Champ | Valeur |
|---|---|
| Domaine | météo, climat |
| URL officielle | https://open-meteo.com |
| Documentation | https://open-meteo.com/en/docs ; conditions : https://open-meteo.com/en/terms |
| Endpoint pertinent | `https://api.open-meteo.com/v1/forecast` (`latitude`, `longitude`, `hourly`, `daily`, `timezone`, `forecast_days` 0–16) |
| Authentification | aucune observée ; la documentation lue ne dit pas si une clé est requise : UNKNOWN |
| Accès gratuit | oui, **non commercial uniquement** |
| Quota gratuit | 600 appels/min, 5 000/h, 10 000/jour, 300 000/mois (page des conditions) |
| Fréquence autorisée | dans ces quotas |
| Fraîcheur | selon le modèle : GFS et HRRR toutes les heures, ICON toutes les 3 h, ECMWF IFS et AIFS toutes les 6 h |
| Historique | UNKNOWN pour cet endpoint (API d'archive distincte, non vérifiée) |
| Couverture | mondiale (les coordonnées sont ramenées à la grille : 40.71, -74.01 demandés → 40.710335, -73.99308 reçus) |
| Langues | sans objet (données numériques) |
| Formats | JSON (défaut), CSV, XLSX |
| Licence | CC BY 4.0, attribution requise |
| Restrictions commerciales | usage commercial interdit en gratuit ; plan payant requis |
| Restrictions de stockage | UNKNOWN |
| Restrictions d'entraînement IA | UNKNOWN |
| Fiabilité et qualité | élevée pour des prévisions de modèles ; ce sont des prévisions, pas des observations officielles de station |
| Coût estimé | 0 en usage non commercial |
| Statut | **VERIFIED_LIMITED** |

**Connecteur actif (2026-11-02)** : `providers/open_meteo.py`. Variables vérifiées par une requête réelle : `temperature_2m_max`, `temperature_2m_min`, `precipitation_sum` (unités `°C`, `mm`), avec `forecast_days` de 1 à 16.

Observé : `200`, JSON avec `daily.time` et `daily.temperature_2m_max` (unité `°C`), `utc_offset_seconds`, `timezone`. Le paramètre `timezone` est obligatoire avec les variables `daily`.

Limites : une prévision n'est pas la mesure qui résout un marché de température. La station et la source de résolution du marché doivent être lues dans ses critères avant tout usage.

### `nasa_eonet` — NASA EONET v3 (catastrophes naturelles)

| Champ | Valeur |
|---|---|
| Domaine | catastrophes naturelles |
| URL officielle | https://eonet.gsfc.nasa.gov |
| Documentation | https://eonet.gsfc.nasa.gov/docs/v3 |
| Endpoints pertinents | `/api/v3/events` (`status`, `limit`, `days`, `category`, `start`, `end`, `bbox`, `magMin`, `magMax`), `/events/geojson`, `/categories`, `/sources` |
| Authentification | aucune observée ; UNKNOWN dans la documentation |
| Accès gratuit | observé gratuit et anonyme |
| Quota gratuit | **UNKNOWN** |
| Fréquence autorisée | UNKNOWN |
| Fraîcheur | UNKNOWN (événements agrégés depuis des sources tierces) |
| Historique | paramètres `start`/`end` et `status=closed` ; profondeur UNKNOWN |
| Couverture | mondiale |
| Langues | anglais |
| Formats | JSON, GeoJSON, RSS, Atom |
| Licence | « disclaimer » renvoyé sans texte : UNKNOWN |
| Restrictions | UNKNOWN |
| Fiabilité et qualité | agrégateur de sources officielles (IRWIN, etc.) ; qualité variable selon la source |
| Coût estimé | 0 |
| Statut | **VERIFIED_FREE** (accès) avec limites et licence `UNKNOWN` |

Observé : `200`. Champs d'un événement : `id`, `title`, `description` (peut être `null`), `link`, `closed` (`null` si ouvert), `categories`, `sources` (`id`, `url`), `geometry` (`date`, `type`, `coordinates`, `magnitudeValue`, `magnitudeUnit`).

### `usgs_earthquakes` — USGS Earthquake Catalog (séismes)

| Champ | Valeur |
|---|---|
| Domaine | catastrophes naturelles, science |
| URL officielle | https://earthquake.usgs.gov |
| Documentation | https://earthquake.usgs.gov/fdsnws/event/1/ ; flux : https://earthquake.usgs.gov/earthquakes/feed/v1.0/geojson.php |
| Endpoints pertinents | `/fdsnws/event/1/query` (`format=geojson`, `starttime`, `endtime`, `minmagnitude`, `limit` ≤ 20 000, `orderby`, `updatedafter`) ; flux `summary/{significant,4.5,2.5,1.0,all}_{hour,day,week,month}.geojson` |
| Authentification | aucune observée ; UNKNOWN dans la documentation |
| Accès gratuit | observé gratuit et anonyme |
| Quota gratuit | aucun quota publié sur les pages lues ; limite de 20 000 événements par requête |
| Fréquence autorisée | UNKNOWN ; la documentation recommande les flux GeoJSON temps réel pour les applications automatiques |
| Fraîcheur | flux « actualisés chaque minute » |
| Historique | catalogue complet via `starttime` ; profondeur non vérifiée |
| Couverture | mondiale |
| Langues | anglais |
| Formats | GeoJSON, QuakeML, CSV, texte (seul GeoJSON vérifié) |
| Licence | UNKNOWN sur les pages lues |
| Fiabilité et qualité | élevée (source primaire) ; magnitudes révisées après coup (`updated`, `status`: `automatic` ou `reviewed`) |
| Coût estimé | 0 |
| Statut | **VERIFIED_FREE** (accès) avec licence et limites de débit `UNKNOWN` |

Observé : `200`. `properties` : `mag`, `place`, `time` et `updated` en millisecondes epoch, `status`, `url`, `detail`, `felt`, `alert`, `tsunami`, `sig`, `net`, `code`, `ids`. Une magnitude peut être révisée : toute observation doit garder son `updated`.

### `hacker_news` — Hacker News API (tendances technologiques)

| Champ | Valeur |
|---|---|
| Domaine | tendances Internet, technologie |
| URL officielle | https://news.ycombinator.com |
| Documentation | https://github.com/HackerNews/API |
| Endpoints pertinents | `https://hacker-news.firebaseio.com/v0/` : `item/<id>.json`, `topstories`, `newstories`, `beststories`, `maxitem`, `updates` |
| Authentification | aucune observée |
| Accès gratuit | oui |
| Quota gratuit | « There is currently no rate limit » (documentation) |
| Fréquence autorisée | aucune limite déclarée ; AMBRE s'impose la sienne (voir `API_LIMITS.md`) |
| Fraîcheur | quasi temps réel |
| Historique | `item/<id>` pour tout identifiant ancien (observé : item 8863, 2007) |
| Couverture | mondiale, communauté technologique anglophone |
| Langues | anglais |
| Formats | JSON ; les champs texte contiennent du HTML |
| Licence | le dépôt de documentation est sous licence MIT ; **licence des données : UNKNOWN** |
| Fiabilité et qualité | mesure d'attention d'une communauté, **pas** de véracité (le score n'est pas une preuve) |
| Coût estimé | 0 |
| Statut | **VERIFIED_FREE** |

Observé : `200` sur `maxitem` et sur `item/8863`. L'API est versionnée et les clients doivent tolérer les champs inconnus.

### `github` — GitHub REST API (publications logicielles)

| Champ | Valeur |
|---|---|
| Domaine | technologie, publications logicielles |
| URL officielle | https://github.com |
| Documentation | https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api |
| Endpoints pertinents | `/repos/{owner}/{repo}/releases/latest`, `/releases`, `/rate_limit` |
| Authentification | optionnelle ; un jeton personnel élève le quota |
| Accès gratuit | oui |
| Quota gratuit | **60 requêtes/h par IP sans authentification** ; 5 000/h avec jeton (documentation) ; limites secondaires : 100 requêtes concurrentes, 900 points/min REST |
| Fraîcheur | immédiate |
| Historique | releases et tags complets |
| Couverture | mondiale |
| Formats | JSON |
| Licence / conditions | conditions de service GitHub ; contenu sous la licence de chaque dépôt : UNKNOWN pour un usage d'entraînement |
| Fiabilité et qualité | élevée pour « une version a été publiée » ; une release n'est pas une annonce officielle de l'éditeur sans vérification du dépôt |
| Coût estimé | 0 |
| Statut | **VERIFIED_LIMITED** (60/h anonyme) ; `REQUIRES_REGISTRATION` pour 5 000/h (jeton à fournir par le propriétaire, jamais dans le code) |

Observé : `200`, `X-RateLimit-Limit: 60`, `X-RateLimit-Remaining: 59`. Les en-têtes `x-ratelimit-*` sont la source d'autorité pour le budget restant.

### `bluesky` — Bluesky / AT Protocol (réseau social)

| Champ | Valeur |
|---|---|
| Domaine | réseaux sociaux |
| URL officielle | https://bsky.app ; documentation : https://docs.bsky.app |
| Endpoint testé | `https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed` (lecture du fil d'un compte public) ; `app.bsky.feed.searchPosts` |
| Authentification | `getAuthorFeed` : aucune (200 observé) ; **`searchPosts` : 403 sur `public.api.bsky.app`, 401 sur `bsky.social`** |
| Accès gratuit | lecture de comptes publics : oui ; recherche : compte requis |
| Quota gratuit | **UNKNOWN** (la page des limites de débit n'a pas pu être lue) |
| Fraîcheur | temps réel |
| Historique | UNKNOWN |
| Formats | JSON |
| Licence / conditions | UNKNOWN (non lues) |
| Fiabilité et qualité | faible comme preuve : un message ou ses « likes » n'est pas un fait ; utile comme signal d'attention et pour suivre des comptes officiels |
| Statut | **VERIFIED_LIMITED** pour `getAuthorFeed` ; **REQUIRES_REGISTRATION** pour la recherche (aucun compte n'est créé sans le propriétaire) |

Conséquence : AMBRE ne peut pas rechercher des mots-clés sur Bluesky sans compte. Il peut lire le fil de comptes officiels dont la liste figure dans la liste blanche.

### `manifold` — Manifold Markets API (marchés)

| Champ | Valeur |
|---|---|
| Domaine | marchés de prédiction |
| Documentation | https://docs.manifold.markets/api |
| Endpoints pertinents | `GET /v0/markets`, `GET /v0/search-markets`, `GET /v0/market/[marketId]` |
| Authentification | aucune pour ces lectures ; requise pour l'écriture (**interdite à AMBRE**) |
| Quota gratuit | 500 requêtes/min par IP (documentation) |
| Licence / conditions | Conditions de service Manifold. Le scraping hors API et le contournement des limites sont interdits. **L'entraînement commercial sur les données de l'API exige une licence de données** ; la recherche académique, les projets personnels et l'usage non commercial sont permis. |
| Statut | **VERIFIED_LIMITED** |

C'est la source déjà utilisée par `integrations/manifold` (lecture seule).

## Candidats non vérifiés

Fournisseurs du catalogue du propriétaire pour lesquels **aucune fiche n'a été complétée** : Marketaux, finlight, NewsData.io, GNews, NewsAPI, The Guardian, FRED, ECB, Eurostat, World Bank, SEC EDGAR, CoinGecko, Mastodon, Reddit, YouTube Data API, Google Trends, TwitterAPI.io, TheSportsDB, football-data.org, API-Football, OpenLigaDB, StatsBomb Open Data, OpenF1, Jolpica F1, NOAA, Meteostat, NASA POWER, OpenWeatherMap, MET Norway, ACLED, ReliefWeb, HDX, OpenAlex, arXiv, Crossref, WHO GHO, CDC, NASA Open APIs.

Statut de tous : `UNVERIFIED`, quota `UNKNOWN`, `enabled: false`. Plusieurs exigent à l'évidence un compte ou une clé (par exemple les API d'actualités à clé, YouTube, Reddit, ACLED) : ce sont des `REQUIRES_REGISTRATION` probables, à confirmer dans leur documentation, et à n'activer qu'avec une clé fournie par le propriétaire. Aucun compte n'est créé par AMBRE.

Ajouts à étudier (non vérifiés) : flux RSS officiels des banques centrales et régulateurs, calendriers officiels de publication (BLS, Eurostat), flux Atom de publications (blogs officiels d'éditeurs). Ils sont adaptés aux marchés qui se résolvent sur une annonce officielle.
