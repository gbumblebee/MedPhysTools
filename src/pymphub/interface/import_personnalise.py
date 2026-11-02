"""Imports personnalisés, proposés par les onglets de tableau et de scalaires."""

from collections.abc import Sequence
from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QInputDialog

from pymphub.importation import (
    ImportImpossible,
    ResultatImport,
    charger_imports_personnalises,
    executer_import_personnalise,
)
from pymphub.interface.messages import message_erreur
from pymphub.interface.pieces import FichierRefuse, refuser_dicom
from pymphub.interface.preferences import dossier_preferences
from pymphub.page import PieceJointe

FICHIER_IMPORTS = "imports_personnalises.py"


class ImportPersonnalise:
    """Choisit une fonction et un fichier, exécute l'import et garde le fichier d'origine.

    L'éditeur qui l'utilise fournit `pieces`, `_signaler` et le signal `valeurs_importees`.
    """

    def _choisir_fichier(self) -> str:
        chemin, _filtre = QFileDialog.getOpenFileName(
            self,
            "Importer un fichier",
            "",
            "Tableaux (*.csv *.txt *.tsv *.xlsx);;Tous (*)",
        )
        return chemin

    def _choisir_import(self, noms: Sequence[str]) -> str | None:
        nom, accepte = QInputDialog.getItem(
            self, "Import personnalisé", "Import :", list(noms)
        )
        return nom if accepte else None

    def executer_import(self) -> tuple[ResultatImport, Path] | None:
        """Demande quoi importer. Une erreur est expliquée ; None si rien n'est importé."""
        fichier = dossier_preferences() / FICHIER_IMPORTS
        try:
            fonctions = charger_imports_personnalises(fichier)
        except ImportImpossible as erreur:
            self._signaler(str(erreur))
            return None
        if not fonctions:
            self._signaler(
                f"Aucun import personnalisé : écrire des fonctions importer_… dans {fichier}."
            )
            return None
        nom = self._choisir_import(sorted(fonctions))
        if nom is None:
            return None
        chemin = self._choisir_fichier()
        if not chemin:
            return None
        source = Path(chemin)
        try:
            # Avant d'exécuter la fonction : aucune donnée de patient ne doit entrer.
            refuser_dicom(source)
            return executer_import_personnalise(nom, fonctions[nom], source), source
        except (ImportImpossible, FichierRefuse) as erreur:
            self._signaler(str(erreur))
            return None

    def _joindre_source(self, source: Path) -> PieceJointe | None:
        """Garde le fichier importé en pièce jointe de la page."""
        if self.pieces is None:
            return None
        try:
            return self.pieces.joindre(source)
        except (ValueError, OSError) as erreur:
            self._signaler(
                f"Import réussi, mais « {source.name} » n'a pas pu être joint à la "
                f"page : {message_erreur(erreur)}"
            )
            return None
