# Imports personnalisés

Pour lire un export d'appareil particulier, écris une fonction dans le fichier `imports_personnalises.py`, dans le dossier des préférences de pymphub :

- **Linux :** `~/.config/pymphub/`
- **Windows :** `%LOCALAPPDATA%\pymphub\`

Dans l'application, le bouton « Import personnalisé… » d'un onglet de tableau propose alors chaque fonction dont le nom commence par `importer_`.

```python
from pathlib import Path


def importer_activimetre(chemin: Path) -> dict[str, list]:
    """Lit l'export texte de l'activimètre : une valeur par ligne après l'en-tête."""
    lignes = chemin.read_text(encoding="cp1252").splitlines()[3:]
    return {
        "activite (MBq)": [float(ligne.split(";")[1]) for ligne in lignes],
        "lot": [ligne.split(";")[0] for ligne in lignes],
    }
```

**Ce que la fonction reçoit :** le chemin du fichier choisi.

**Ce qu'elle renvoie :** un dictionnaire de colonnes, comme ci-dessus, ou une liste de lignes dont la première peut contenir les en-têtes.

**Règles de lecture :**
- Une unité entre parenthèses dans un en-tête est reprise, si elle est reconnue.
- Une colonne contenant `007` reste un identifiant.
- Les incertitudes restent à déclarer dans l'application.

**Sécurité :** ce fichier exécute du code Python avec tes droits. C'est pourquoi pymphub ne le cherche que dans ton profil, jamais dans une archive : ouvrir une archive reçue de quelqu'un d'autre n'exécute rien. N'y copie que du code que tu comprends.
