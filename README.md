# MedPhysTools

Outils du physicien médical. **MedPhysNotebook** en est la première application : un cahier de labo avec une archive versionnée. Le paquet Python s'appelle `medphystools`.



## Lancer le logiciel

- **Windows, sans fenêtre de commande :** après un premier `uv sync`, double-cliquer sur `lanceurs/windows/medphysnotebook.vbs`. `creer_raccourci_bureau.vbs` met sur le Bureau un raccourci avec le logo. Si le service bloque les `.vbs`, `lancer.bat` fait la même chose. En cas d'arrêt sur une erreur, le détail est dans `%TEMP%\medphysnotebook_journal.txt`.
- **Linux :** `uv run medphysnotebook`, ou `lanceurs/linux/installer_lanceur.sh` pour l'ajouter à la liste des applications.
- **Changer le logo :** remplacer `src/medphystools/interface/ressources/logo.svg`, puis lancer `uv run python outils/generer_logo.py`.
