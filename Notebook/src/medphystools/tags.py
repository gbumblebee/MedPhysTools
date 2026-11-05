"""Tags et titres dans le texte des notes : `{{activite}}` affiche la valeur, U et k.

La note ne garde que le tag. La valeur se calcule à l'affichage, avec les règles d'un calcul :
elle suit donc les corrections de la page, qui restent tracées avec leur motif. Un tag qu'on
ne sait pas lire reste visible et marqué : il n'est jamais remplacé par du vide.

Une ligne qui commence par `# `, `## ` ou `### ` est un titre, à l'affichage seulement.

`$A_0 e^{-t}$` est une équation, dessinée à l'affichage. Le `$` ouvrant colle à la formule et
le `$` fermant aussi : « entre 5$ et 10$ » reste du texte, et `\\$` écrit un dollar. Une
équation illisible reste visible et marquée, comme un tag.
"""

import html
import re
from collections.abc import Callable
from dataclasses import dataclass, field

import pandas as pd

from medphystools.calculs import ErreurCalcul, calculer
from medphystools.equations import (
    RESOLUTION_ECRAN,
    EquationIllisible,
    dessiner_equation,
)
from medphystools.page import Identifiant, Page

_TAG = re.compile(r"\{\{(.*?)\}\}", flags=re.DOTALL)
_OPTIONS = ("valeur", "U", "unite")
TITRE = re.compile(r"^(#{1,3})[ \t]+(.*)$", flags=re.DOTALL)
_EQUATION = re.compile(
    r"(?<![\\$])(\$\$?)(?![\s$])((?:\\.|[^$\n\\])+?)(?<!\s)\1(?![\d$])"
)
_TAILLE_TEXTE = 10.0
_TAILLES_TITRES = {1: 14.0, 2: 12.0, 3: 10.5}
_STYLES_TITRES = {
    1: "font-size:14pt;font-weight:700;margin-top:12px;margin-bottom:4px",
    2: "font-size:12pt;font-weight:700;margin-top:10px;margin-bottom:3px",
    3: "font-size:10.5pt;font-weight:700;margin-top:8px;margin-bottom:2px",
}


@dataclass(frozen=True)
class Morceau:
    """Un bout de note : du texte, ou la valeur d'un tag. `erreur` marque un tag cassé."""

    texte: str
    erreur: bool = False
    # Une valeur de tag n'est pas du texte saisi : elle ne peut pas devenir un titre.
    valeur: bool = field(default=False, compare=False)


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
                Morceau(
                    valeur_du_tag(trouve.group(1), page, lire_tableau, k), valeur=True
                )
            )
        except ErreurCalcul:
            morceaux.append(
                Morceau(
                    f"{{{{{trouve.group(1).strip()} ?}}}}", erreur=True, valeur=True
                )
            )
        position = trouve.end()
    if position < len(texte):
        morceaux.append(Morceau(texte[position:]))
    return morceaux


def note_en_texte(morceaux: list[Morceau]) -> str:
    """Assemble une note en texte simple."""
    return "".join(morceau.texte for morceau in morceaux)


def _marque_erreur(texte: str, couleur: str) -> str:
    return f'<span style="color:{couleur};font-weight:600">{texte}</span>'


def _texte_html(
    texte: str, couleur_erreur: str, resolution: float, taille: float = _TAILLE_TEXTE
) -> str:
    """Écrit du texte saisi en HTML : il est échappé, et ses équations sont dessinées."""
    parties: list[str] = []
    position = 0
    for trouve in _EQUATION.finditer(texte):
        parties.append(
            html.escape(texte[position : trouve.start()].replace(r"\$", "$"))
        )
        try:
            image = dessiner_equation(trouve.group(2), taille, resolution)
        except EquationIllisible:
            parties.append(_marque_erreur(html.escape(trouve.group(0)), couleur_erreur))
        else:
            parties.append(
                f'<img src="{image.adresse()}" width="{image.largeur}" '
                f'height="{image.hauteur}" align="middle" '
                f'alt="{html.escape(trouve.group(2), quote=True)}">'
            )
        position = trouve.end()
    parties.append(html.escape(texte[position:].replace(r"\$", "$")))
    return "".join(parties)


def _lignes_html(
    morceaux: list[Morceau], couleur_erreur: str, resolution: float
) -> list[tuple[str | None, str]]:
    """Découpe une note en lignes : (début saisi de la ligne, reste en HTML).

    Le début saisi vaut None si la ligne commence par une valeur de tag.
    """
    lignes: list[tuple[str | None, str]] = [("", "")]
    for morceau in morceaux:
        if morceau.valeur:
            texte = html.escape(morceau.texte).replace("\n", "<br>")
            if morceau.erreur:
                texte = _marque_erreur(texte, couleur_erreur)
            debut, reste = lignes[-1]
            lignes[-1] = (None if debut == "" and not reste else debut, reste + texte)
            continue
        premier, *suivants = morceau.texte.split("\n")
        debut, reste = lignes[-1]
        if debut is not None and not reste:
            lignes[-1] = (debut + premier, "")
        else:
            lignes[-1] = (
                debut,
                reste + _texte_html(premier, couleur_erreur, resolution),
            )
        lignes.extend((suivant, "") for suivant in suivants)
    return lignes


def note_en_html(
    morceaux: list[Morceau],
    couleur_erreur: str = "#a4372a",
    resolution: float = RESOLUTION_ECRAN,
) -> str:
    """Assemble une note en HTML : le texte saisi ne peut pas être pris pour une mise en forme.

    `resolution` est celle du dessin des équations : celle de l'écran, ou de l'impression.
    """
    parties: list[str] = []
    texte: list[str] = []

    def fermer_texte() -> None:
        if texte:
            parties.append(
                f'<div style="white-space:pre-wrap">{"<br>".join(texte)}</div>'
            )
            texte.clear()

    for debut, reste in _lignes_html(morceaux, couleur_erreur, resolution):
        titre = TITRE.match(debut) if debut else None
        if titre is not None and (titre.group(2).strip() or reste):
            # Le titre a ses marges : une ligne vide juste avant ferait double emploi.
            while texte and not texte[-1]:
                texte.pop()
            fermer_texte()
            niveau = len(titre.group(1))
            contenu = _texte_html(
                titre.group(2), couleur_erreur, resolution, _TAILLES_TITRES[niveau]
            )
            parties.append(
                f'<div style="{_STYLES_TITRES[niveau]}">{contenu}{reste}</div>'
            )
        else:
            texte.append(_texte_html(debut or "", couleur_erreur, resolution) + reste)
    fermer_texte()
    return "".join(parties)
