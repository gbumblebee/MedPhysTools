#!/bin/sh
# Ajoute MedPhysNotebook, avec son logo, à la liste des applications de l'utilisateur.
# Pour le retirer : rm ~/.local/share/applications/medphysnotebook.desktop
set -eu
racine=$(cd "$(dirname "$0")/../.." && pwd)
dossier="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
mkdir -p "$dossier"
# Le lanceur de l'ancien nom du logiciel ne fonctionne plus.
rm -f "$dossier/pymphub.desktop"
cat > "$dossier/medphysnotebook.desktop" <<FIN
[Desktop Entry]
Type=Application
Name=MedPhysNotebook
Comment=Cahier de laboratoire du physicien médical
Exec="$racine/.venv/bin/medphysnotebook"
Icon=$racine/src/medphystools/interface/ressources/logo.png
Terminal=false
Categories=Science;
StartupWMClass=medphysnotebook
FIN
echo "MedPhysNotebook ajouté à la liste des applications : $dossier/medphysnotebook.desktop"
