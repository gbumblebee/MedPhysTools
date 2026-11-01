"""Vérifie le socle : le cœur s'importe et ne dépend jamais de Qt."""

import ast
import subprocess
import sys
from pathlib import Path

import pytest

import pymphub

BIBLIOTHEQUES_QT = {
    "PySide2",
    "PySide6",
    "PyQt5",
    "PyQt6",
    "qtpy",
    "magicgui",
    "pyqtgraph",
}


def imports_qt(source: str) -> set[str]:
    """Renvoie les bibliothèques Qt importées par un code source Python."""
    trouvees: set[str] = set()
    for noeud in ast.walk(ast.parse(source)):
        if isinstance(noeud, ast.Import):
            noms = [alias.name for alias in noeud.names]
        elif isinstance(noeud, ast.ImportFrom) and noeud.module and noeud.level == 0:
            noms = [noeud.module]
        else:
            continue
        trouvees |= {nom.split(".")[0] for nom in noms} & BIBLIOTHEQUES_QT
    return trouvees


@pytest.mark.parametrize(
    "source",
    [
        "import PySide6",
        "import os, PySide6.QtCore",
        "from PySide6.QtWidgets import QApplication",
        "def f():\n    import pyqtgraph",
    ],
)
def test_un_import_qt_est_detecte(source):
    assert imports_qt(source)


def test_un_import_ordinaire_n_est_pas_detecte():
    assert not imports_qt("import json\nfrom pathlib import Path")


def test_les_sources_du_coeur_n_importent_pas_qt():
    racine = Path(pymphub.__file__).parent
    fichiers = sorted(racine.rglob("*.py"))
    assert fichiers

    fautifs = {
        str(fichier.relative_to(racine)): imports
        for fichier in fichiers
        if (imports := imports_qt(fichier.read_text(encoding="utf-8")))
    }
    assert not fautifs


def test_importer_pymphub_ne_charge_pas_qt():
    # Dans un processus neuf, pour ne pas dépendre de ce que pytest a déjà importé.
    code = "import sys, pymphub; print(' '.join(sys.modules))"
    resultat = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    modules = {nom.split(".")[0] for nom in resultat.stdout.split()}
    assert not modules & BIBLIOTHEQUES_QT
