"""Préférences de l'utilisateur : un petit fichier TOML dans son profil.

Elles ne demandent aucun droit administrateur et ne vont jamais dans une archive. Elles
sont facultatives : un fichier absent, abîmé ou impossible à écrire n'empêche rien.
"""

import os
import tomllib
from pathlib import Path

import tomli_w
from PySide6.QtCore import QStandardPaths

from pymphub.disque import ecrire_atomique


def dossier_preferences() -> Path:
    """Renvoie le dossier des préférences. `PYMPHUB_CONFIG` permet de le déplacer."""
    choisi = os.environ.get("PYMPHUB_CONFIG")
    if choisi:
        return Path(choisi)
    base = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.GenericConfigLocation
    )
    return Path(base) / "pymphub"


def _lire() -> dict[str, object]:
    try:
        return tomllib.loads(
            (dossier_preferences() / "preferences.toml").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return {}


def derniere_archive() -> Path | None:
    """Renvoie le chemin de la dernière archive ouverte, s'il est connu."""
    chemin = _lire().get("derniere_archive")
    return Path(chemin) if isinstance(chemin, str) and chemin else None


def memoriser_derniere_archive(chemin: str) -> None:
    """Retient la dernière archive ouverte. Un échec d'écriture est sans conséquence."""
    contenu = _lire() | {"derniere_archive": chemin}
    try:
        dossier = dossier_preferences()
        dossier.mkdir(parents=True, exist_ok=True)
        ecrire_atomique(dossier / "preferences.toml", tomli_w.dumps(contenu))
    except OSError:
        return
