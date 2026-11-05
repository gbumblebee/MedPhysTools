# MedPhysTools (application MedPhysNotebook) — consignes pour Claude Code

Le code de MedPhysNotebook est dans Notebook/ : lancer les commandes depuis ce dossier.

Spécification complète : Notebook/docs/cahier-des-charges.md (à lire avant toute tâche).

## Conventions
- Langue : docstrings, commentaires et noms métier en français.
- Les modules d'analyse sont des fonctions pures à entrées typées (section 8).
- Le cœur (Notebook/src/medphystools/) ne dépend jamais de Qt : seul le sous-paquet medphystools.interface l'importe.
- Données brutes jamais modifiées ; tout changement est versionné ; le motif est obligatoire sur une page validée.
- Annotations de type obligatoires ; code simple, lisible par un physicien.

## Commandes
- Tests : uv run pytest
- Lint et formatage : uv run ruff check . && uv run ruff format .
- Ajouter une dépendance : uv add <paquet> (jamais pip install)

## Méthode
- Travailler phase par phase (section 14), petite étape par petite étape.
- Écrire les tests avec le code ; ne pas passer à la suite si les tests échouent.
