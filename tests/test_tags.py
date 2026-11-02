"""Vérifie les tags des notes : `{{activite}}` affiche la valeur, et la note garde le tag."""

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from pymphub.incertitudes import Composante
from pymphub.page import ColonneNumerique, Identifiant, Page, Scalaire, Tableau
from pymphub.tags import decouper_note, note_en_html, note_en_texte, valeur_du_tag

HEURE = datetime(2026, 11, 14, 9, 30, tzinfo=timezone(timedelta(hours=1)))


def composante(valeur, unite):
    return Composante(type="B", valeur=valeur, unite=unite, source="s")


def page_de(valeur=100.0):
    return Page(
        id="2026-11-14_tags_0001",
        titre="Tags",
        date_mesure=HEURE,
        date_saisie=HEURE,
        auteur="G.",
        mesures=(
            Scalaire(
                nom="activite",
                valeur=valeur,
                unite="MBq",
                composantes=(composante(1.0, "MBq"),),
            ),
            Scalaire(
                nom="ratio", valeur=0.5, unite="1", composantes=(composante(0.01, "1"),)
            ),
            Identifiant(nom="lot", valeur="007"),
            Tableau(
                nom="t",
                fichier="brut/t.csv",
                colonnes=(
                    ColonneNumerique(
                        nom="a", unite="MBq", composantes=(composante(2.0, "MBq"),)
                    ),
                ),
            ),
        ),
    )


def lire(_nom):
    return pd.DataFrame({"a": [10.0, 20.0]})


def rendu(texte, page=None, k=2.0):
    return note_en_texte(decouper_note(texte, page or page_de(), lire, k))


def test_un_tag_affiche_la_valeur_son_incertitude_et_k():
    assert rendu("A = {{activite}}.") == "A = 100 ± 2 MBq (k = 2)."


def test_la_note_garde_le_tag_et_l_affichage_suit_la_valeur():
    texte = "A = {{activite}}"
    assert rendu(texte, page_de(100.0)) == "A = 100 ± 2 MBq (k = 2)"
    assert rendu(texte, page_de(200.0)) == "A = 200 ± 2 MBq (k = 2)"
    assert texte == "A = {{activite}}"


def test_le_tag_suit_le_facteur_k():
    assert rendu("{{activite}}", k=3.0) == "100 ± 3 MBq (k = 3)"


@pytest.mark.parametrize(
    ("tag", "attendu"),
    [
        ("activite:valeur", "100 MBq"),
        ("activite:U", "2 MBq"),
        ("activite:unite", "MBq"),
        ("ratio:valeur", "0.5"),
        (" activite ", "100 ± 2 MBq (k = 2)"),
        ("activite * 2", "200 ± 4 MBq (k = 2)"),
        ("activite / t.a[1]", "10 ± 4 (k = 2)"),
        ("t.a[2]", "20 ± 4 MBq (k = 2)"),
        ("lot", "007"),
    ],
)
def test_ce_qu_un_tag_peut_afficher(tag, attendu):
    assert valeur_du_tag(tag, page_de(), lire, 2.0) == attendu


def test_un_tag_casse_se_voit_sans_arreter_le_reste():
    morceaux = decouper_note("a {{absent}} b {{activite:valeur}}", page_de(), lire)
    assert [m.texte for m in morceaux] == ["a ", "{{absent ?}}", " b ", "100 MBq"]
    assert [m.erreur for m in morceaux] == [False, True, False, False]


@pytest.mark.parametrize(
    "tag",
    [
        "{{}}",
        "{{ }}",
        "{{__import__('os')}}",
        "{{t.a}}",
        "{{t}}",
        "{{activite + }}",
        "{{activite:autre}}",
    ],
)
def test_un_tag_illisible_reste_visible_et_ne_s_execute_pas(tag):
    morceaux = decouper_note(tag, page_de(), lire)
    assert len(morceaux) == 1
    assert morceaux[0].erreur
    assert morceaux[0].texte.endswith("?}}")


@pytest.mark.parametrize("texte", ["", "sans tag", "{ {a} }", "{{ouvert", "fermé}}"])
def test_un_texte_sans_tag_ne_change_pas(texte):
    assert rendu(texte) == texte


def test_plusieurs_lignes_restent_plusieurs_lignes():
    assert rendu("a\n{{activite:valeur}}\nb") == "a\n100 MBq\nb"


def test_le_html_echappe_le_texte_saisi_et_marque_les_tags_casses():
    html = note_en_html(decouper_note("<b>x</b> {{absent}}\nfin", page_de(), lire))
    assert "<b>" not in html
    assert "&lt;b&gt;" in html
    assert "color:#a4372a" in html
    assert "<br>" in html


def test_une_valeur_ne_peut_pas_injecter_de_mise_en_forme():
    page = page_de().model_copy(
        update={"mesures": (Identifiant(nom="lot", valeur="<img src=x>"),)}
    )
    assert "<img" not in note_en_html(decouper_note("{{lot}}", page))
