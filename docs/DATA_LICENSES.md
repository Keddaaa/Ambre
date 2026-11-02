# Licences et conditions d'usage des données

Vérification du **2026-11-02**, d'après les pages officielles lues. Ce n'est pas un avis juridique. Là où la licence n'a pas été trouvée, elle est `UNKNOWN` et **AMBRE ne stocke ni ne réutilise ces données au-delà de ce que la fiche autorise**.

Contexte d'usage d'AMBRE : projet personnel de recherche en simulation (Mana virtuel), non commercial, sans mise en ligne des données.

| Fournisseur | Licence ou conditions | Commercial | Redistribution | Entraînement IA | Obligation |
|---|---|---|---|---|---|
| GDELT | usage libre « for any academic, commercial, or governmental use » (page About) ; licence nommée UNKNOWN | oui | oui | non mentionné : UNKNOWN | citer GDELT avec un lien |
| Open-Meteo | CC BY 4.0 (gratuit) | **non** en gratuit | selon CC BY 4.0 | UNKNOWN | attribution |
| NASA EONET | « disclaimer » non lu : UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN |
| USGS | non lue : UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN |
| Hacker News | dépôt de documentation sous MIT ; données : UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN |
| GitHub | conditions de service GitHub ; contenu sous la licence de chaque dépôt | UNKNOWN | UNKNOWN | UNKNOWN | respecter les limites de débit |
| Bluesky | non lues : UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN |
| Manifold | conditions de service ; **entraînement commercial sur les données de l'API : licence requise** (contact `data@manifold.markets`) ; non commercial et recherche permis | licence requise pour entraîner | UNKNOWN | recherche et usage personnel permis | pas de scraping hors API, pas de contournement des limites |

Conséquences pour la conception :

1. Chaque observation garde `source_id`, `source_url` et la date de récupération, ce qui permet la citation (GDELT, Open-Meteo) et le retrait.
2. Les champs `allow_commercial_use` et `allow_ai_training` du registre valent `false` ou `null` (inconnu) par défaut. `null` est traité comme `false` pour tout ce qui sortirait du cadre personnel.
3. L'entraînement d'un modèle sur des contenus textuels bruts n'est pas prévu : le modèle d'AMBRE reçoit des **signaux structurés** (nombres, drapeaux, dates), pas des articles.
4. Si le projet devient commercial, Open-Meteo et Manifold exigent d'abord un plan ou une licence payants. À décider par le propriétaire.
