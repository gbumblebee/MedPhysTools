"""Historique des pages : comparaison champ par champ et messages de commit.

Git est le seul historique des corrections. Chaque message de commit nomme donc la
page, les champs modifiés et le motif, pour retrouver facilement chaque correction.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

# Nature d'un commit créé tout seul, pour un fichier changé hors du logiciel.
NATURE_EXTERNE = "modification externe"

# Clé qui identifie un élément dans une liste : son nom plutôt que sa position.
_CLES_IDENTIFIANTES = ("nom", "id", "chemin")


@dataclass(frozen=True)
class Modification:
    """Un champ modifié entre deux versions d'une page."""

    champ: str
    ancien: Any
    nouveau: Any


@dataclass(frozen=True)
class EntreeHistorique:
    """Une version d'une page dans son historique."""

    commit: str
    date: datetime
    auteur: str
    nature: str
    page: str
    motif: str | None
    modifications: tuple[Modification, ...]


@dataclass(frozen=True)
class CorrectionChamp:
    """Une correction motivée d'un champ d'une page."""

    ancien: Any
    nouveau: Any
    motif: str | None
    date: datetime
    auteur: str


def corrections_par_champ(
    corrections: Sequence[EntreeHistorique],
) -> dict[str, list[CorrectionChamp]]:
    """Regroupe des corrections par champ modifié, de la plus ancienne à la plus récente."""
    resultat: dict[str, list[CorrectionChamp]] = {}
    for entree in corrections:
        for modification in entree.modifications:
            resultat.setdefault(modification.champ, []).append(
                CorrectionChamp(
                    modification.ancien,
                    modification.nouveau,
                    entree.motif,
                    entree.date,
                    entree.auteur,
                )
            )
    return resultat


def _cle_de_liste(elements: list[Any]) -> str | None:
    """Renvoie la clé qui identifie les éléments d'une liste, s'il y en a une."""
    for cle in _CLES_IDENTIFIANTES:
        if all(isinstance(e, dict) and cle in e for e in elements):
            return cle
    return None


def _comparer(ancien: Any, nouveau: Any, chemin: str) -> list[Modification]:  # noqa: ANN401
    if ancien == nouveau:
        return []
    if isinstance(ancien, dict) and isinstance(nouveau, dict):
        modifications: list[Modification] = []
        for cle in [*ancien, *(c for c in nouveau if c not in ancien)]:
            sous_chemin = f"{chemin}.{cle}" if chemin else str(cle)
            if cle not in ancien:
                modifications.append(Modification(sous_chemin, None, nouveau[cle]))
            elif cle not in nouveau:
                modifications.append(Modification(sous_chemin, ancien[cle], None))
            else:
                modifications.extend(_comparer(ancien[cle], nouveau[cle], sous_chemin))
        return modifications

    if isinstance(ancien, list) and isinstance(nouveau, list):
        cle = _cle_de_liste(ancien + nouveau)
        if cle is not None:
            ordre_ancien = [e[cle] for e in ancien]
            ordre_nouveau = [e[cle] for e in nouveau]
            modifications = _comparer(
                {e[cle]: e for e in ancien}, {e[cle]: e for e in nouveau}, chemin
            )
            if not modifications and ordre_ancien != ordre_nouveau:
                return [Modification(f"{chemin} (ordre)", ordre_ancien, ordre_nouveau)]
            return modifications
        if len(ancien) == len(nouveau) and all(
            isinstance(e, dict) for e in ancien + nouveau
        ):
            modifications = []
            for i, (a, b) in enumerate(zip(ancien, nouveau)):
                modifications.extend(_comparer(a, b, f"{chemin}[{i}]"))
            return modifications

    return [Modification(chemin, ancien, nouveau)]


def differences(
    ancienne: dict[str, Any] | None, nouvelle: dict[str, Any]
) -> tuple[Modification, ...]:
    """Compare deux versions d'une page, données sous forme de JSON déjà lu.

    Une grandeur est repérée par son nom : `mesures.activite.valeur`. Sans version
    précédente, il n'y a aucune modification à signaler.
    """
    if ancienne is None:
        return ()
    return tuple(_comparer(ancienne, nouvelle, ""))


def normaliser_motif(motif: str | None) -> str | None:
    """Ramène un motif à une seule ligne ; renvoie None s'il est vide."""
    if motif is None:
        return None
    return " ".join(motif.split()) or None


def message_commit(
    nature: str,
    page: str | None = None,
    motif: str | None = None,
    modifications: tuple[Modification, ...] = (),
    sujet: str | None = None,
) -> str:
    """Compose un message de commit lisible et facile à relire par le logiciel."""
    if sujet is None:
        sujet = f"{nature} : {motif if nature == 'correction' else page}"
    lignes = [sujet, "", f"Nature: {nature}"]
    if page is not None:
        lignes.append(f"Page: {page}")
    if modifications:
        # Un fichier modifié à la main peut avoir n'importe quel nom de champ, même sur deux
        # lignes : il ne doit pas pouvoir imiter les autres informations du message.
        champs = ", ".join(" ".join(m.champ.split()) for m in modifications)
        lignes.append(f"Champs: {champs}")
    if motif is not None:
        lignes.append(f"Motif: {motif}")
    # Les fichiers lourds sont hors de Git : le commit garde au moins la trace du changement.
    lignes += [
        f"Fichier: {m.champ} : {m.ancien} -> {m.nouveau}"
        for m in modifications
        if m.champ.endswith(("empreinte", "empreinte_pixels"))
    ]
    return "\n".join(lignes) + "\n"


def lire_message(message: str) -> dict[str, str]:
    """Relit les informations structurées d'un message de commit."""
    _, _, corps = message.partition("\n\n")
    return dict(
        re.findall(r"^(Nature|Page|Champs|Motif): (.*)$", corps, flags=re.MULTILINE)
    )
