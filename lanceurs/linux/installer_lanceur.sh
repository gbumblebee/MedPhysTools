#!/bin/sh
# Ajoute pymphub, avec son logo, à la liste des applications de l'utilisateur.
# Pour le retirer : rm ~/.local/share/applications/pymphub.desktop
set -eu
racine=$(cd "$(dirname "$0")/../.." && pwd)
dossier="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
mkdir -p "$dossier"
cat > "$dossier/pymphub.desktop" <<FIN
[Desktop Entry]
Type=Application
Name=pymphub
Comment=Cahier de laboratoire du physicien médical
Exec="$racine/.venv/bin/pymphub"
Icon=$racine/src/pymphub/interface/ressources/logo.png
Terminal=false
Categories=Science;
StartupWMClass=pymphub
FIN
echo "pymphub ajouté à la liste des applications : $dossier/pymphub.desktop"
