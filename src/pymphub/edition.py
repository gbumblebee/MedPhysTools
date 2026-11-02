"""Édition d'une page, sans interface : fonctions pures utilisées par l'espace Saisie."""

import math
import re
from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any

from pymphub.archive import Archive
from pymphub.page import (
    Fichier,
    GrandeurDerivee,
    Identifiant,
    Onglet,
    Page,
    Scalaire,
    Tableau,
)

Grandeur = Scalaire | Identifiant | Tableau | Fichier | GrandeurDerivee
_DECIMALES_ANNOTATION = 6


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


def point_normalise(
    x: float, y: float, largeur: float, hauteur: float
) -> tuple[float, float]:
    """Ramène un point de l'écran entre 0 et 1, pour qu'il ne dépende pas de l'affichage.

    Un point sorti de l'image revient sur son bord. L'arrondi garde un `page.json` lisible,
    et reste bien plus fin qu'un pixel, même sur une très grande photo.
    """
    return (
        round(min(max(x / largeur, 0.0), 1.0), _DECIMALES_ANNOTATION),
        round(min(max(y / hauteur, 0.0), 1.0), _DECIMALES_ANNOTATION),
    )


def nom_libre(texte: str, pris: Iterable[str], defaut: str = "fichier") -> str:
    """Propose un nom de grandeur libre dans la page : rien n'est jamais écrasé."""
    base = suggerer_nom(texte) or defaut
    occupes = set(pris)
    nom = base
    numero = 1
    while nom in occupes:
        numero += 1
        nom = f"{base}_{numero}"
    return nom


def lire_nombre(texte: str) -> float:
    """Lit un nombre saisi au clavier ou collé, avec un point ou une virgule décimale.

    Lève `ValueError` pour un texte qui n'est pas un nombre fini. Un séparateur de milliers
    mêlé à une décimale (`1,234.5`) est refusé plutôt que deviné.
    """
    nettoye = re.sub(r"[\s\u00a0\u202f]", "", texte)
    if "," in nettoye and "." in nettoye:
        raise ValueError(f"« {texte} » : utiliser un seul séparateur décimal.")
    try:
        valeur = float(nettoye.replace(",", "."))
    except ValueError as erreur:
        raise ValueError(f"« {texte} » n'est pas un nombre.") from erreur
    if not math.isfinite(valeur):
        raise ValueError(f"« {texte} » n'est pas un nombre fini.")
    return valeur


def lire_serie(texte: str) -> list[float]:
    """Lit une série de valeurs collée : séparées par des lignes, des espaces, ou « ; ».

    Une virgule reste une décimale, comme dans un tableur français.
    """
    return [lire_nombre(morceau) for morceau in re.split(r"[\s;]+", texte) if morceau]


def suggerer_nom(texte: str) -> str | None:
    """Propose un nom utilisable dans une formule, ou None s'il n'y a rien à garder."""
    nom = re.sub(r"\W+", "_", texte.strip()).strip("_")
    if not nom:
        return None
    return nom if nom.isidentifier() else f"g_{nom}"


def fusionner_grandeurs(
    anciennes: Sequence[Grandeur],
    possedees: Iterable[str],
    nouvelles: Sequence[Grandeur],
) -> list[Grandeur]:
    """Applique à la liste des grandeurs ce qu'un éditeur a saisi.

    Une grandeur possédée par l'éditeur est remplacée à sa place, ou retirée si elle n'est
    plus dans `nouvelles`. Les nouvelles grandeurs vont à la fin. Les autres ne changent pas.
    """
    a_retirer = set(possedees)
    par_nom = {grandeur.nom: grandeur for grandeur in nouvelles}
    resultat = [
        par_nom.pop(grandeur.nom, grandeur)
        if grandeur.nom in par_nom or grandeur.nom not in a_retirer
        else None
        for grandeur in anciennes
    ]
    return [g for g in resultat if g is not None] + list(par_nom.values())


def conflits_de_noms(
    anciennes: Sequence[Grandeur],
    possedees: Iterable[str],
    nouvelles: Sequence[Grandeur],
) -> list[str]:
    """Liste les noms qui écraseraient une autre grandeur si on fusionnait cette saisie.

    Il y a conflit si deux saisies différentes portent le même nom, ou si une saisie prend
    le nom d'une grandeur de la page qu'aucun éditeur ne possède, comme celle d'un onglet
    retiré ou un tableau.
    """
    a_remplacer = set(possedees)
    existantes = {g.nom for g in anciennes} - a_remplacer
    conflits: set[str] = set()
    vues: dict[str, Grandeur] = {}
    for grandeur in nouvelles:
        if grandeur.nom in existantes:
            conflits.add(grandeur.nom)
        if grandeur.nom in vues and vues[grandeur.nom] != grandeur:
            conflits.add(grandeur.nom)
        vues[grandeur.nom] = grandeur
    return sorted(conflits)
