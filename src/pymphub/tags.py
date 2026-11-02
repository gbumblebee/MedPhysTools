"""Tags dans le texte des notes : `{{activite}}` affiche la valeur, U et k.

La note ne garde que le tag. La valeur se calcule à l'affichage, avec les règles d'un calcul :
elle suit donc les corrections de la page, qui restent tracées avec leur motif. Un tag qu'on
ne sait pas lire reste visible et marqué : il n'est jamais remplacé par du vide.
"""

import html
import re
from collections.abc import Callable
from dataclasses import dataclass

import pandas as pd

from pymphub.calculs import ErreurCalcul, calculer
from pymphub.page import Identifiant, Page

_TAG = re.compile(r"\{\{(.*?)\}\}", flags=re.DOTALL)
_OPTIONS = ("valeur", "U", "unite")


@dataclass(frozen=True)
class Morceau:
    """Un bout de note : du texte, ou la valeur d'un tag. `erreur` marque un tag cassé."""

    texte: str
    erreur: bool = False


def _unite_ecrite(unite: str) -> str:
    return "" if unite == "1" else f" {unite}"


def valeur_du_tag(
    tag: str,
    page: Page,
    lire_tableau: Callable[[str], pd.DataFrame] | None,
    k: float,
) -> str:
    """Écrit la valeur d'un tag. Lève `ErreurCalcul` si elle ne peut pas être lue."""
    expression, separateur, option = tag.strip().rpartition(":")
    if not separateur or option.strip() not in _OPTIONS:
        expression, option = tag.strip(), ""
    expression = expression.strip()
    option = option.strip()
    grandeur = next((g for g in page.mesures if g.nom == expression), None)
    if isinstance(grandeur, Identifiant):
        return grandeur.valeur
    budget = calculer(page, expression, lire_tableau)
    unite = _unite_ecrite(budget.unite)
    if option == "valeur":
        return f"{budget.valeur:.6g}{unite}"
    if option == "U":
        return f"{budget.elargie(k):.2g}{unite}"
    if option == "unite":
        return budget.unite
    return f"{budget.valeur:.6g} ± {budget.elargie(k):.2g}{unite} (k = {k:g})"


def decouper_note(
    texte: str,
    page: Page,
    lire_tableau: Callable[[str], pd.DataFrame] | None = None,
    k: float = 2.0,
) -> list[Morceau]:
    """Découpe une note en texte et en valeurs. Un tag cassé n'arrête jamais le reste."""
    morceaux: list[Morceau] = []
    position = 0
    for trouve in _TAG.finditer(texte):
        if trouve.start() > position:
            morceaux.append(Morceau(texte[position : trouve.start()]))
        try:
            morceaux.append(
                Morceau(valeur_du_tag(trouve.group(1), page, lire_tableau, k))
            )
        except ErreurCalcul:
            morceaux.append(
                Morceau(f"{{{{{trouve.group(1).strip()} ?}}}}", erreur=True)
            )
        position = trouve.end()
    if position < len(texte):
        morceaux.append(Morceau(texte[position:]))
    return morceaux


def note_en_texte(morceaux: list[Morceau]) -> str:
    """Assemble une note en texte simple."""
    return "".join(morceau.texte for morceau in morceaux)


def note_en_html(morceaux: list[Morceau], couleur_erreur: str = "#a4372a") -> str:
    """Assemble une note en HTML : le texte saisi ne peut pas être pris pour une mise en forme."""
    parties = []
    for morceau in morceaux:
        texte = html.escape(morceau.texte).replace("\n", "<br>")
        if morceau.erreur:
            texte = (
                f'<span style="color:{couleur_erreur};font-weight:600">{texte}</span>'
            )
        parties.append(texte)
    return f'<div style="white-space:pre-wrap">{"".join(parties)}</div>'
