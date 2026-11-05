"""Vérifie les tags des notes : `{{activite}}` affiche la valeur, et la note garde le tag."""

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from medphystools.incertitudes import Composante
from medphystools.page import ColonneNumerique, Identifiant, Page, Scalaire, Tableau
from medphystools.tags import decouper_note, note_en_html, note_en_texte, valeur_du_tag

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


# --- Titres ---------------------------------------------------------------------------


def _page_pour_titres(valeur: str = "A1") -> Page:
    autres = [g for g in page_de().mesures if g.nom != "lot"]
    return page_de().model_copy(
        update={"mesures": (*autres, Identifiant(nom="lot", valeur=valeur))}
    )


def _html_note(texte: str, page: Page | None = None) -> str:
    return note_en_html(decouper_note(texte, page or _page_pour_titres()))


def test_une_ligne_diese_devient_un_titre_de_son_niveau():
    rendu = _html_note("# Montage\ntexte\n## Détail\n### Fin")
    assert (
        '<div style="font-size:14pt;font-weight:700;margin-top:12px;margin-bottom:4px">Montage</div>'
        in rendu
    )
    assert (
        '<div style="font-size:12pt;font-weight:700;margin-top:10px;margin-bottom:3px">Détail</div>'
        in rendu
    )
    assert (
        '<div style="font-size:10.5pt;font-weight:700;margin-top:8px;margin-bottom:2px">Fin</div>'
        in rendu
    )
    assert "#" not in rendu.replace("font", "")
    assert '<div style="white-space:pre-wrap">texte</div>' in rendu


def test_une_note_sans_titre_garde_son_rendu():
    assert _html_note("a\n\nb") == '<div style="white-space:pre-wrap">a<br><br>b</div>'


@pytest.mark.parametrize(
    "texte", ["#3 mesures", "#", "####  trop", "texte # pas titre", "# "]
)
def test_ce_qui_n_est_pas_un_titre_reste_du_texte(texte):
    rendu = _html_note(texte)
    assert rendu == f'<div style="white-space:pre-wrap">{texte}</div>'


def test_un_titre_peut_contenir_un_tag():
    page = _page_pour_titres()
    rendu = _html_note("## Activité {{lot}} ici", page)
    assert ">Activité A1 ici</div>" in rendu
    assert "font-size:12pt" in rendu


def test_un_titre_seulement_fait_d_un_tag_reste_un_titre():
    rendu = _html_note("# {{lot}}", _page_pour_titres())
    assert "font-size:14pt" in rendu
    assert ">A1</div>" in rendu


def test_une_valeur_de_tag_ne_devient_jamais_un_titre():
    page = _page_pour_titres(valeur="# piège")
    rendu = _html_note("{{lot}}\nsuite", page)
    assert "font-size" not in rendu
    assert "# piège<br>suite" in rendu


def test_un_titre_echappe_le_html():
    rendu = _html_note("# <b>x</b> & y")
    assert "&lt;b&gt;x&lt;/b&gt; &amp; y" in rendu
    assert "<b>" not in rendu


def test_la_ligne_vide_avant_un_titre_ne_s_ajoute_pas_a_ses_marges():
    rendu = _html_note("avant\n\n# Titre")
    assert rendu.startswith('<div style="white-space:pre-wrap">avant</div>')


def test_le_texte_de_la_note_garde_ses_diezes():
    assert (
        note_en_texte(decouper_note("# Titre\nx", _page_pour_titres())) == "# Titre\nx"
    )


# --- Équations ------------------------------------------------------------------------


def test_une_equation_devient_une_image():
    rendu = _html_note(r"Activité : $A_0 e^{-\lambda t}$ fin")
    assert 'src="data:image/png;base64,' in rendu
    assert rendu.count("<img") == 1
    assert "Activité : <img" in rendu
    assert " fin</div>" in rendu
    assert r'alt="A_0 e^{-\lambda t}"' in rendu


def test_le_dollar_double_est_aussi_une_equation():
    assert "<img" in _html_note("$$x^2$$")


@pytest.mark.parametrize(
    "texte",
    [
        "entre 5$ et 10$",
        "de $5 à $10",
        "$ x $",
        "prix : 5$",
        "$",
        "$$",
        r"\$x\$",
        "$a\nb$",
    ],
)
def test_ce_qui_n_est_pas_une_equation_reste_du_texte(texte):
    assert "<img" not in _html_note(texte)
    assert "Équation" not in _html_note(texte)


def test_un_dollar_echappe_s_ecrit_sans_sa_barre():
    assert _html_note(r"\$x\$") == '<div style="white-space:pre-wrap">$x$</div>'


def test_une_equation_illisible_reste_visible_et_marquee():
    rendu = _html_note(r"avant $\inconnu{x}$ après")
    assert "<img" not in rendu
    assert r"$\inconnu{x}$" in rendu
    assert "color:#a4372a" in rendu
    assert "avant " in rendu and " après" in rendu


def test_une_equation_trop_longue_est_marquee_sans_dessin():
    rendu = _html_note("$" + "x" * 501 + "$")
    assert "<img" not in rendu
    assert "color:#a4372a" in rendu


def test_une_equation_illisible_echappe_le_html():
    rendu = _html_note(r"$\inconnu<b>$")
    assert "&lt;b&gt;" in rendu
    assert "<b>" not in rendu


def test_une_equation_a_cote_d_un_tag_et_dans_un_titre():
    rendu = _html_note("# Loi $x^2$\n$y^2$ pour {{lot}}")
    assert rendu.count("<img") == 2
    assert "font-size:14pt" in rendu
    assert " pour A1" in rendu


def test_l_equation_d_un_titre_suit_sa_taille():
    titre = _html_note("# $x$")
    texte = _html_note("$x$")
    hauteur = lambda rendu: int(rendu.split('height="')[1].split('"')[0])  # noqa: E731
    assert hauteur(titre) > hauteur(texte)


def test_une_valeur_de_tag_ne_devient_jamais_une_equation():
    rendu = _html_note("{{lot}}", _page_pour_titres(valeur="$x$"))
    assert "<img" not in rendu
    assert "$x$" in rendu


def test_le_texte_de_la_note_garde_sa_formule():
    assert note_en_texte(decouper_note("$x^2$", _page_pour_titres())) == "$x^2$"
