# Feuille de route

Ces éléments n'existent **pas** en 0.1. Ils ne sont ni simulés ni représentés par des fichiers vides.

## 0.2 (suggéré)

- Historique des paris (`GET /v0/bets`).
- Courbe de calibration dans le dashboard et métriques par catégorie / horizon (Brier, log loss et calibration par tranche existent déjà, voir `EVALUATION.md`).
- Backtesting chronologique minimal et protocole de validation reproductible.
- Exécuter la suite de tests sur PostgreSQL (la migration et un cycle réel sont déjà vérifiés).

## Navigation et veille autonomes (nécessité)

Conception et fournisseurs vérifiés : `INTELLIGENCE_ARCHITECTURE.md`, `API_CATALOG.md`, `API_LIMITS.md`, `DATA_LICENSES.md` (ADR-010). Implémentation par phases B à G.

Ambre ne peut pas rester un simple lecteur de l'API Manifold. Un marché de prédiction se déplace avec l'information : sans accès à des sources externes, l'estimation de probabilité ne peut que répéter le prix du marché ou un taux de base (voir ADR-004), donc ne peut pas démontrer d'avantage. La capacité de s'informer et de réagir est une condition pour que le projet atteigne son objectif, pas une option.

**Principe :** liberté de navigation large à l'intérieur d'un périmètre borné.

- **Liberté :** Ambre choisit lui-même quoi chercher, quoi lire et quand réagir (nouvelle information sur un marché suivi, changement brutal de prix, résolution proche).
- **Périmètre :** Ambre ne consulte que les réseaux et sources d'une **liste blanche**. La liste blanche n'est pas encore définie ; elle sera fournie par le propriétaire du projet et inscrite dans `configs/` puis dans un ADR. Tant qu'elle est vide, aucune navigation n'est possible.
- **Lecture seule :** la navigation ne publie, ne commente et ne se connecte nulle part. Les interdictions de `AGENTS.md` restent inchangées : aucun endpoint d'écriture Manifold, aucun trading en monnaie réelle, mode `PAPER` uniquement.
- **Contenu non fiable :** tout ce qui vient du web est une donnée, jamais une instruction. Une réponse de LLM ou une page lue n'est pas une probabilité (voir « IA contextuelle »).
- **Traçabilité :** chaque consultation est journalisée (source, date de récupération, contenu utilisé). Une donnée absente reste `None`, aucune date n'est inventée.
- **Isolation :** l'agent qui navigue s'exécute dans un environnement isolé (WSL2 ou conteneur), sans secret ni accès aux fichiers personnels.
- **Validation :** l'apport de ces sources n'est affirmé qu'après mesure hors échantillon (Brier, log loss, backtesting), jamais supposé.

Ce module n'existe pas en 0.1 et n'est pas représenté par un fichier vide. Il sera créé avec son document (`WEB_INTELLIGENCE.md`), ses tests sans Internet (fixtures `synthetic_`) et son ADR.

## Plus tard

- **Imitation sélective** des participants (`IMITATION_ENGINE.md` à créer avec le module).
- **IA contextuelle** : contexte et sources, sans traiter une réponse de LLM comme une probabilité. S'appuie sur la navigation et veille autonomes ci-dessus.
- **Apprentissage continu contrôlé** (`LEARNING_ENGINE.md`).
- **Backtesting complet** (`BACKTESTING.md`).
- Carnet d'ordres limites dans la simulation CPMM.
- Synchro par WebSocket avec reconnexion (0.1 fait du polling).
- Moteur de survie étendu : exploration, spécialisation, récupération, fonction de récompense.
- Authentification de l'API.
- Pages du dashboard non alimentées en 0.1 : analyse de stratégies, modèles, backtests, apprentissage, imitation, alertes.
