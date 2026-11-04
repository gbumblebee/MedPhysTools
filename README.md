

## Lancer le logiciel

- **Windows, sans fenêtre de commande :** après un premier `uv sync`, double-cliquer sur `lanceurs/windows/pymphub.vbs`. `creer_raccourci_bureau.vbs` met sur le Bureau un raccourci avec le logo. Si le service bloque les `.vbs`, `lancer.bat` fait la même chose. En cas d'arrêt sur une erreur, le détail est dans `%TEMP%\pymphub_journal.txt`.
- **Linux :** `uv run pymphub`, ou `lanceurs/linux/installer_lanceur.sh` pour l'ajouter à la liste des applications.
- **Changer le logo :** remplacer `src/pymphub/interface/ressources/logo.svg`, puis lancer `uv run python outils/generer_logo.py`.
