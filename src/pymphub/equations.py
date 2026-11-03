"""Équations des notes : une formule mathtext devient une image, sans installer LaTeX.

mathtext, le moteur de matplotlib, comprend une partie de la syntaxe LaTeX : fractions,
indices, lettres grecques, racines, sommes et intégrales. Il ne connaît ni les matrices ni
les équations alignées.
"""

import base64
import io
import struct
from dataclasses import dataclass
from functools import lru_cache

_LONGUEUR_MAX = 500
RESOLUTION_ECRAN = 96.0
RESOLUTION_IMPRESSION = 300.0
_POINTS_PAR_POUCE = 72
_COULEUR = "#1f2933"


class EquationIllisible(ValueError):
    """La formule ne peut pas être dessinée."""


@dataclass(frozen=True)
class ImageEquation:
    """Une équation dessinée : image PNG, et sa taille d'affichage en pixels."""

    png: bytes
    largeur: int
    hauteur: int

    def adresse(self) -> str:
        """L'image sous forme d'adresse `data:`, à placer directement dans du HTML."""
        return "data:image/png;base64," + base64.b64encode(self.png).decode("ascii")


@lru_cache(maxsize=256)
def dessiner_equation(
    formule: str, taille: float = 10.0, resolution: float = RESOLUTION_ECRAN
) -> ImageEquation:
    """Dessine une formule, sans ses `$`, à la taille de texte donnée en points.

    Sur écran, Qt réduit les images sans les lisser, ce qui rend les indices illisibles : il
    faut y dessiner à la résolution réelle de l'écran, pour afficher l'image pixel pour pixel.

    Lève `EquationIllisible` si mathtext ne la comprend pas.
    """
    # Importé ici : relire une page ne doit pas charger matplotlib.
    from matplotlib.backends.backend_agg import FigureCanvasAgg  # noqa: PLC0415
    from matplotlib.figure import Figure  # noqa: PLC0415
    from matplotlib.font_manager import FontProperties  # noqa: PLC0415
    from matplotlib.mathtext import MathTextParser  # noqa: PLC0415

    if len(formule) > _LONGUEUR_MAX:
        raise EquationIllisible(
            f"Équation trop longue : {_LONGUEUR_MAX} caractères au plus."
        )
    texte = f"${formule}$"
    police = FontProperties(size=taille)
    try:
        largeur, hauteur, profondeur, _, _ = MathTextParser("path").parse(
            texte, dpi=_POINTS_PAR_POUCE, prop=police
        )
        if largeur <= 0 or hauteur <= 0:
            raise EquationIllisible("Cette équation ne dessine rien.")
        figure = Figure(
            figsize=(largeur / _POINTS_PAR_POUCE, hauteur / _POINTS_PAR_POUCE)
        )
        FigureCanvasAgg(figure)
        figure.text(
            0, profondeur / hauteur, texte, fontproperties=police, color=_COULEUR
        )
        sortie = io.BytesIO()
        figure.savefig(sortie, dpi=resolution, format="png", transparent=True)
    except EquationIllisible:
        raise
    except (ValueError, RecursionError, OverflowError, MemoryError) as erreur:
        raise EquationIllisible(f"Équation non reconnue : {formule}") from erreur
    png = sortie.getvalue()
    # L'en-tête d'un PNG donne sa taille : largeur et hauteur à partir de l'octet 16.
    pixels_large, pixels_haut = struct.unpack(">II", png[16:24])
    return ImageEquation(
        png=png,
        largeur=round(pixels_large * RESOLUTION_ECRAN / resolution),
        hauteur=round(pixels_haut * RESOLUTION_ECRAN / resolution),
    )
