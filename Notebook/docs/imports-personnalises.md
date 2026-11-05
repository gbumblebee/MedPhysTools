# Imports personnalisés

Pour lire un export d'appareil particulier, écris une fonction dans le fichier `imports_personnalises.py`, dans le dossier des préférences de MedPhysNotebook :

- **Linux :** `~/.config/medphysnotebook/`
- **Windows :** `%LOCALAPPDATA%\medphysnotebook\`

Dans l'application, le bouton « Import personnalisé… » d'un onglet de tableau ou de scalaires propose alors chaque fonction dont le nom commence par `importer_`.

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
- Une colonne sans incertitude déclarée reçoit l'incertitude nulle par défaut, à remplacer dans l'application si besoin.

## Importer aussi des scalaires et des informations

Pour lire aussi ce qui se trouve autour du tableau, la fonction renvoie un dictionnaire avec au moins une de ces trois clés :

```python
def importer_activimetre(chemin: Path) -> dict:
    return {
        "tableau": {"heure (h)": [0, 2], "activite (MBq)": [100.0, 79.4]},
        "scalaires": [
            {"nom": "temperature", "valeur": 21.5, "unite": "degC"},
            {
                "nom": "activite_ref",
                "valeur": 100.0,
                "unite": "MBq",
                "commentaire": "Valeur du certificat",
                "composantes": [
                    {"type": "B", "valeur": 1.0, "unite": "MBq", "k": 2.0, "source": "certificat"}
                ],
            },
        ],
        "informations": {"numero_serie": "NS-007"},
    }
```

- **`tableau`** : les colonnes ou les lignes décrites plus haut. Il est facultatif.
- **`scalaires`** : `nom` et `valeur` obligatoires ; `unite`, `commentaire` et `composantes` facultatifs. Les champs d'une composante sont décrits dans `FORMAT.md`.
- **`informations`** : des textes gardés à l'identique, `007` reste `007`.

Scalaires et informations arrivent dans l'onglet de scalaires de la page, créé s'il n'existe pas. Le fichier d'origine est gardé en pièce jointe.

**Une valeur sans unité** s'affiche, signalée « à compléter », mais ne s'enregistre pas tant qu'elle n'est pas complétée. **Une valeur sans incertitude** reçoit l'incertitude nulle par défaut, que le bouton de sa ligne et le cahier affichent comme telle.

**Un nom déjà pris dans la page** n'écrase rien : la nouvelle ligne s'appelle `temperature_2`, et c'est à toi de garder la bonne.

**Sécurité :** ce fichier exécute du code Python avec tes droits. C'est pourquoi MedPhysNotebook ne le cherche que dans ton profil, jamais dans une archive : ouvrir une archive reçue de quelqu'un d'autre n'exécute rien. N'y copie que du code que tu comprends.
