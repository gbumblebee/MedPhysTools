"""Édition d'une page, sans interface : fonctions pures utilisées par l'espace Saisie."""

from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any

from pymphub.archive import Archive
from pymphub.page import Onglet, Page


def modifier_champs(page: Page, **champs: Any) -> Page:  # noqa: ANN401
    """Renvoie une copie de la page avec ces champs, après avoir vérifié toutes ses règles.

    Lève `pydantic.ValidationError` si le résultat n'est pas une page valide.
    """
    return Page.model_validate(page.model_dump() | champs)


def nouvel_id_onglet(onglets: Iterable[Onglet]) -> str:
    """Propose un identifiant d'onglet libre, jamais réutilisé tant que l'onglet existe."""
    pris = {onglet.id for onglet in onglets}
    numero = len(pris) + 1
    while f"onglet_{numero}" in pris:
        numero += 1
    return f"onglet_{numero}"


def decouper_tags(texte: str) -> tuple[str, ...]:
    """Lit une liste de tags séparés par des virgules, sans doublon ni case vide."""
    return tuple(dict.fromkeys(tag.strip() for tag in texte.split(",") if tag.strip()))


def date_saisie_vers_mesure(
    saisie: datetime, ancienne: datetime | None = None
) -> datetime:
    """Donne un fuseau à une date saisie sans fuseau.

    Si elle n'a pas changé à la minute près, l'ancienne date est conservée telle quelle, avec
    son fuseau et ses secondes. Sinon, c'est le fuseau de cet ordinateur à cette date.
    """
    if ancienne is not None:
        affichee = ancienne.replace(tzinfo=None, second=0, microsecond=0)
        if affichee == saisie.replace(second=0, microsecond=0):
            return ancienne
    return saisie.astimezone()


def nouvelle_page(
    archive: Archive,
    titre: str,
    date_mesure: datetime,
    auteur: str,
    machine: str | None = None,
    tags: Sequence[str] = (),
    description: str = "",
    onglets: Sequence[Onglet] = (),
) -> Page:
    """Prépare une page libre en brouillon, sans encore l'enregistrer."""
    return Page(
        id=archive.nouvel_identifiant(date_mesure, titre),
        titre=titre,
        description=description,
        date_mesure=date_mesure,
        date_saisie=datetime.now().astimezone(),
        auteur=auteur,
        machine=machine,
        tags=tuple(tags),
        onglets=tuple(onglets),
    )
