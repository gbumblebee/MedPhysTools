"""Vérifie le dessin des équations des notes, sans Qt."""

import base64
import struct
import subprocess
import sys

import pytest

from pymphub.equations import (
    RESOLUTION_IMPRESSION,
    EquationIllisible,
    dessiner_equation,
)


def test_une_formule_donne_un_png_a_sa_taille():
    image = dessiner_equation(r"\frac{a}{b}")
    assert image.png.startswith(b"\x89PNG")
    assert image.largeur > 0
    assert image.hauteur > 0
    assert base64.b64decode(image.adresse().split(",")[1]) == image.png


def test_une_formule_plus_grande_donne_une_image_plus_grande():
    assert dessiner_equation("x", 14.0).hauteur > dessiner_equation("x", 10.0).hauteur


@pytest.mark.parametrize(
    "formule", [r"\inconnu", r"\begin{matrix}a\end{matrix}", r"\ ", "x" * 501]
)
def test_une_formule_impossible_est_refusee(formule):
    with pytest.raises(EquationIllisible):
        dessiner_equation(formule)


def test_les_equations_ne_chargent_pas_qt():
    code = (
        "import sys; from pymphub.equations import dessiner_equation;"
        "dessiner_equation('x');"
        "assert not any(m.startswith('PySide6') for m in sys.modules)"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


def test_a_l_ecran_l_image_s_affiche_pixel_pour_pixel():
    image = dessiner_equation(r"A_0 e^{-\lambda t}")
    largeur, hauteur = struct.unpack(">II", image.png[16:24])
    assert (image.largeur, image.hauteur) == (largeur, hauteur)


def test_a_l_impression_la_taille_d_affichage_ne_change_pas():
    ecran = dessiner_equation("x^2")
    impression = dessiner_equation("x^2", 10.0, RESOLUTION_IMPRESSION)
    assert len(impression.png) > len(ecran.png)
    assert abs(impression.largeur - ecran.largeur) <= 1
    assert abs(impression.hauteur - ecran.hauteur) <= 1
