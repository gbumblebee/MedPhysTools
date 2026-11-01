# pymphub — consignes pour Claude Code

Spécification complète : docs/cahier-des-charges.md (à lire avant toute tâche).

## Conventions
- Langue : docstrings, commentaires et noms métier en français.
- Les modules d'analyse sont des fonctions pures à entrées typées (section 8).
- Le cœur (src/pymphub/) ne dépend jamais de Qt.
- Données brutes jamais modifiées ; toute correction est versionnée avec un motif.
- Annotations de type obligatoires ; code simple, lisible par un physicien.

## Commandes
- Tests : uv run pytest
- Lint et formatage : uv run ruff check . && uv run ruff format .
- Ajouter une dépendance : uv add <paquet> (jamais pip install)

## Méthode
- Travailler phase par phase (section 14), petite étape par petite étape.
- Écrire les tests avec le code ; ne pas passer à la suite si les tests échouent.
