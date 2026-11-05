"""Dates et heures saisies dans un tableau ou une formule : toujours avec leur fuseau.

Le fuseau évite une erreur d'une heure sur une décroissance au changement d'heure. Le fichier
garde l'écriture ISO 8601. L'écran montre l'écriture française, et ne cite le fuseau que s'il
diffère de celui de cet ordinateur : ainsi, relire ce qui est affiché redonne la même date.
"""

import re
from datetime import datetime

_FRANCAISE = re.compile(
    r"^(\d{1,2})/(\d{1,2})/(\d{4})[ T]+(\d{1,2}):(\d{2})(?::(\d{2})(\.\d+)?)?"
    r"\s*(Z|[+-]\d{2}:\d{2})?$"
)
_EXEMPLE = "04/11/2026 14:30"


class DateInvalide(ValueError):
    """Ce texte n'est pas une date et une heure : le message donne un exemple."""


def _vers_iso(texte: str) -> str:
    francaise = _FRANCAISE.match(texte)
    if francaise is None:
        return texte
    jour, mois, annee, heure, minute, seconde, fraction, fuseau = francaise.groups()
    return (
        f"{annee}-{int(mois):02d}-{int(jour):02d}T{int(heure):02d}:{minute}:"
        f"{seconde or '00'}{fraction or ''}{fuseau or ''}"
    )


def lire_date(texte: str, fuseau_requis: bool = False) -> datetime:
    """Lit une date et une heure, en écriture française ou ISO 8601.

    Sans fuseau, c'est celui de cet ordinateur à cette date. `fuseau_requis` refuse ce cas :
    une formule doit donner le même résultat sur tous les ordinateurs.
    """
    propre = texte.strip()
    try:
        date = datetime.fromisoformat(_vers_iso(propre))
    except ValueError as erreur:
        raise DateInvalide(
            f"« {propre} » n'est pas une date et une heure : écrire par exemple {_EXEMPLE}."
        ) from erreur
    if ":" not in propre:
        raise DateInvalide(f"« {propre} » : il manque l'heure, comme dans {_EXEMPLE}.")
    if date.tzinfo is not None:
        return date
    if fuseau_requis:
        raise DateInvalide(
            f"« {propre} » : indiquer le fuseau, comme dans 2026-11-04T14:30:00+01:00."
        )
    return date.astimezone()


def lire_date_iso(texte: str) -> datetime:
    """Lit une cellule enregistrée, qui doit porter son fuseau."""
    return lire_date(texte, fuseau_requis=True)


def ecrire_date_iso(date: datetime) -> str:
    """L'écriture enregistrée : `2026-11-04T14:30:05+01:00`."""
    return date.isoformat()


def afficher_date(date: datetime) -> str:
    """L'écriture à l'écran : `04/11/2026 14:30:05`, et le fuseau s'il n'est pas le nôtre."""
    texte = date.strftime("%d/%m/%Y %H:%M:%S")
    if date.microsecond:
        texte += f".{date.microsecond:06d}"
    if date.utcoffset() != date.replace(tzinfo=None).astimezone().utcoffset():
        texte += f" {date.isoformat()[-6:]}"
    return texte


def maintenant() -> datetime:
    """L'heure actuelle à la seconde près, avec le fuseau de cet ordinateur."""
    return datetime.now().astimezone().replace(microsecond=0)
