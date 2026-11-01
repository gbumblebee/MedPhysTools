"""Écriture sûre sur disque et règles de nommage compatibles avec Windows."""

import os
import re
import tempfile
from pathlib import Path

_NOMS_RESERVES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_CARACTERES_INTERDITS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_LONGUEUR_MAX = 255


class NomInvalide(ValueError):
    """Le nom de fichier ou de dossier ne serait pas valide sous Windows."""


def verifier_nom_windows(nom: str) -> str:
    """Refuse un nom invalide sous Windows, y compris quand on travaille sous Linux.

    Une archive doit pouvoir être copiée d'un système à l'autre sans perdre de fichier.
    """
    if not nom or nom in {".", ".."}:
        raise NomInvalide(f"Nom « {nom} » invalide.")
    if len(nom) > _LONGUEUR_MAX:
        raise NomInvalide(
            f"Nom trop long ({len(nom)} caractères, {_LONGUEUR_MAX} au plus)."
        )
    if _CARACTERES_INTERDITS.search(nom):
        raise NomInvalide(f"Nom « {nom} » invalide : caractère interdit sous Windows.")
    if nom[-1] in {".", " "}:
        raise NomInvalide(
            f"Nom « {nom} » invalide : il finit par un point ou une espace."
        )
    if nom.split(".")[0].upper() in _NOMS_RESERVES:
        raise NomInvalide(f"Nom « {nom} » invalide : nom réservé par Windows.")
    return nom


def ecrire_atomique(chemin: Path, texte: str) -> None:
    """Écrit un fichier texte en entier ou pas du tout.

    Le texte va d'abord dans un fichier temporaire du même dossier, qui remplace ensuite
    la cible d'un seul coup. Une coupure de courant laisse ainsi l'ancien fichier intact.
    Le texte est en UTF-8 avec des fins de ligne LF, quel que soit le système.
    """
    descripteur, temporaire = tempfile.mkstemp(
        dir=chemin.parent, prefix=f".{chemin.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(descripteur, "wb") as fichier:
            fichier.write(texte.encode("utf-8"))
            fichier.flush()
            os.fsync(fichier.fileno())
        os.replace(temporaire, chemin)
    except BaseException:
        Path(temporaire).unlink(missing_ok=True)
        raise
