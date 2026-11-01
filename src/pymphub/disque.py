"""Écriture sûre sur disque et règles de nommage compatibles avec Windows."""

import ntpath
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


def prefixer_chemin_windows(chemin: str) -> str:
    r"""Ajoute à un chemin Windows absolu le préfixe qui lève la limite de 260 caractères.

    `D:\a` devient `\\?\D:\a`, et `\\serveur\partage` devient `\\?\UNC\serveur\partage`.
    Ce préfixe ne demande aucun droit administrateur, contrairement au réglage système
    `LongPathsEnabled`. Avec lui, Windows n'interprète plus le chemin : il doit donc
    déjà être normalisé, sans `/` ni `..`, ce que fait cette fonction.
    """
    if chemin.startswith(("\\\\?\\", "\\\\.\\")):
        return chemin
    normalise = ntpath.normpath(chemin)
    if normalise.startswith("\\\\"):
        return f"\\\\?\\UNC\\{normalise[2:]}"
    return f"\\\\?\\{normalise}"


def chemin_long(chemin: str | Path) -> Path:
    """Renvoie un chemin utilisable au-delà de 260 caractères sous Windows.

    Sous les autres systèmes, qui n'ont pas cette limite, il est renvoyé tel quel. Le
    préfixe n'est jamais stocké dans une archive : il sert seulement à accéder au disque.
    """
    if os.name == "nt":
        return Path(prefixer_chemin_windows(os.path.abspath(chemin)))
    return Path(chemin)


def chemin_affichable(chemin: str | Path) -> str:
    """Retire le préfixe des chemins longs, pour un message ou un outil externe."""
    texte = str(chemin)
    if texte.startswith("\\\\?\\UNC\\"):
        return f"\\\\{texte[8:]}"
    if texte.startswith("\\\\?\\"):
        return texte[4:]
    return texte
