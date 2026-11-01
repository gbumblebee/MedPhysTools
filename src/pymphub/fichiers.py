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

_TAILLE_BLOC = 1024 * 1024


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
