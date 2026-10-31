# Feuille de route

Ces éléments n'existent **pas** en 0.1. Ils ne sont ni simulés ni représentés par des fichiers vides.

## 0.2 (suggéré)

- Historique des paris (`GET /v0/bets`).
- Métriques Brier et log loss sur les marchés résolus ; courbe de calibration.
- Backtesting chronologique minimal et protocole de validation reproductible.
- Vérification de la migration PostgreSQL (Docker).

## Plus tard

- **Imitation sélective** des participants (`IMITATION_ENGINE.md` à créer avec le module).
- **IA contextuelle** : contexte et sources, sans traiter une réponse de LLM comme une probabilité.
- **Apprentissage continu contrôlé** (`LEARNING_ENGINE.md`).
- **Backtesting complet** (`BACKTESTING.md`).
- Carnet d'ordres limites dans la simulation CPMM.
- Synchro par WebSocket avec reconnexion (0.1 fait du polling).
- Moteur de survie étendu : exploration, spécialisation, récupération, fonction de récompense.
- Authentification de l'API.
- Pages du dashboard non alimentées en 0.1 : analyse de stratégies, modèles, backtests, apprentissage, imitation, alertes.
