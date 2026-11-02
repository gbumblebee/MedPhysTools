"""Fichiers bruts : empreintes et magasin `objets/` adressé par contenu.

Un fichier copié dans le magasin porte le nom de son empreinte SHA-256. Une série
utilisée par plusieurs pages n'y est donc stockée qu'une fois, et ne peut pas changer
sans que cela se voie.
"""

import hashlib
import os
import stat
import tempfile
from pathlib import Path
from typing import Literal

_TAILLE_BLOC = 1024 * 1024
_SUFFIXES_DICOM = {".dcm", ".dicom"}
_POSITION_MARQUE_DICOM = 128

EtatFichier = Literal["intact", "altéré", "manquant", "illisible", "non vérifié"]
# Les états à signaler : « non vérifié » dit seulement qu'on n'a pas calculé l'empreinte.
ETATS_ALERTE: frozenset[str] = frozenset({"altéré", "manquant", "illisible"})


class FichierManquant(FileNotFoundError):
    """Le fichier attendu est introuvable."""


class FichierAltere(ValueError):
    """Le contenu du fichier ne correspond plus à son empreinte."""


class ReferenceNonResolue(LookupError):
    """La référence existe mais ne peut pas être résolue en un fichier local."""


def empreinte_fichier(chemin: Path) -> str:
    """Calcule l'empreinte SHA-256 d'un fichier, sans le charger entièrement en mémoire."""
    calcul = hashlib.sha256()
    try:
        with open(chemin, "rb") as fichier:
            while bloc := fichier.read(_TAILLE_BLOC):
                calcul.update(bloc)
    except (FileNotFoundError, NotADirectoryError, IsADirectoryError) as erreur:
        raise FichierManquant(f"Fichier introuvable : « {chemin} ».") from erreur
    return calcul.hexdigest()


def verifier_empreinte(chemin: Path, attendue: str) -> Path:
    """Renvoie le chemin si le fichier est intact, sans rien écrire sur le disque."""
    obtenue = empreinte_fichier(chemin)
    if obtenue != attendue:
        raise FichierAltere(
            f"Le fichier « {chemin} » a changé : empreinte {obtenue} "
            f"au lieu de {attendue}."
        )
    return chemin


def etat_fichier(
    chemin: Path, empreinte: str, taille_max: int | None = None
) -> EtatFichier:
    """Dit si un fichier correspond à son empreinte, sans jamais lever d'erreur.

    Au-delà de `taille_max` octets, l'empreinte n'est pas calculée et l'état vaut
    « non vérifié » : un affichage ne doit pas rester bloqué sur un très gros fichier.
    """
    try:
        if taille_max is not None and chemin.stat().st_size > taille_max:
            return "non vérifié"
        return "intact" if empreinte_fichier(chemin) == empreinte else "altéré"
    except (FichierManquant, FileNotFoundError, NotADirectoryError):
        return "manquant"
    except OSError:
        return "illisible"


def semble_dicom(chemin: Path) -> bool:
    """Reconnaît un fichier DICOM à son extension ou à la marque « DICM » de son en-tête."""
    if chemin.suffix.lower() in _SUFFIXES_DICOM:
        return True
    try:
        with open(chemin, "rb") as fichier:
            fichier.seek(_POSITION_MARQUE_DICOM)
            return fichier.read(4) == b"DICM"
    except OSError:
        return False


def chemin_objet(magasin: Path, empreinte: str) -> Path:
    """Renvoie l'emplacement d'un objet : `objets/3f/a9c2…`."""
    return magasin / empreinte[:2] / empreinte[2:]


def copier_dans_le_magasin(magasin: Path, source: Path) -> str:
    """Copie un fichier dans le magasin s'il n'y est pas déjà, et renvoie son empreinte.

    La copie passe par un fichier temporaire, et l'empreinte est calculée sur ce qui est
    réellement écrit. L'objet final est en lecture seule : une donnée brute ne se
    modifie pas.
    """
    calcul = hashlib.sha256()
    descripteur, temporaire = tempfile.mkstemp(
        dir=magasin, prefix=".import.", suffix=".tmp"
    )
    try:
        with os.fdopen(descripteur, "wb") as destination:
            try:
                with open(source, "rb") as origine:
                    while bloc := origine.read(_TAILLE_BLOC):
                        calcul.update(bloc)
                        destination.write(bloc)
            except (FileNotFoundError, NotADirectoryError, IsADirectoryError) as erreur:
                raise FichierManquant(
                    f"Fichier introuvable : « {source} »."
                ) from erreur
            destination.flush()
            os.fsync(destination.fileno())

        empreinte = calcul.hexdigest()
        cible = chemin_objet(magasin, empreinte)
        if cible.exists():
            # Un objet déjà présent doit être intact : sinon, cette page partirait d'un
            # fichier abîmé sans que personne ne le sache.
            verifier_empreinte(cible, empreinte)
            Path(temporaire).unlink()
            return empreinte

        cible.parent.mkdir(exist_ok=True)
        os.chmod(temporaire, stat.S_IREAD)
        os.replace(temporaire, cible)
        return empreinte
    except BaseException:
        Path(temporaire).unlink(missing_ok=True)
        raise
