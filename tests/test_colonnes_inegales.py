"""Un tableau n'a pas forcément des colonnes de même longueur : rien n'est perdu ni tronqué."""

import math
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pytest

from medphystools import Archive
from medphystools.ajustements import lire_points
from medphystools.calculs import ContexteCalcul, ErreurCalcul, creer_colonne
from medphystools.export import SansImages, page_en_html
from medphystools.graphes import preparer_graphe
from medphystools.incertitudes import Composante
from medphystools.interface.espace_saisie import DescriptionPage, EspaceSaisie
from medphystools.page import (
    ColonneDate,
    ColonneIdentifiant,
    ColonneIncertitude,
    ColonneNumerique,
    Page,
    ReglagesGraphe,
    Tableau,
)
from medphystools.tableaux import ecrire_csv, lire_csv

HEURE = datetime(2026, 11, 14, 9, 30, tzinfo=timezone(timedelta(hours=1)))
MBQ = Composante(type="B", valeur=1.0, unite="MBq", source="certificat")
H = Composante(type="B", valeur=0.1, unite="h", source="horloge")


def tableau_mixte() -> Tableau:
    return Tableau(
        nom="t",
        fichier="brut/t.csv",
        colonnes=(
            ColonneNumerique(nom="heure", unite="h", composantes=(H,)),
            ColonneNumerique(nom="act", unite="MBq", composantes=(MBQ,)),
            ColonneIdentifiant(nom="lot"),
            ColonneDate(nom="quand"),
        ),
    )


DONNEES = {
    "heure": [0.0, 1.0, 2.0, 3.0],
    "act": [100.0, 89.0],
    "lot": ["007"],
    "quand": ["2026-11-04T14:30:05+01:00", None, "2026-11-04T16:30:05+01:00"],
}


@pytest.fixture
def arc(tmp_path):
    return Archive.creer(tmp_path / "A", "CHU X", "Resp.", date(2026, 11, 1))


@pytest.fixture
def page_inegale(arc):
    identifiant = arc.nouvel_identifiant(HEURE, "p")
    arc.creer_page(
        Page(
            id=identifiant,
            titre="Colonnes inégales",
            date_mesure=HEURE,
            date_saisie=HEURE,
            auteur="G.",
            mesures=(tableau_mixte(),),
        ),
        tableaux={"t": DONNEES},
    )
    return identifiant


@pytest.fixture
def espace(qtbot, arc, monkeypatch):
    resultat = EspaceSaisie()
    qtbot.addWidget(resultat)
    resultat.installer(arc)
    resultat.erreurs = []
    monkeypatch.setattr(resultat, "afficher_erreur", resultat.erreurs.append)
    assert resultat.creer_page(DescriptionPage("Saisie", HEURE, "G.", None, ()))
    return resultat


# --- CSV ----------------------------------------------------------------------------------


def test_les_colonnes_courtes_sont_completees_par_des_cellules_vides():
    texte = ecrire_csv(tableau_mixte(), DONNEES)
    assert texte.splitlines() == [
        "heure,act,lot,quand",
        "0.0,100.0,007,2026-11-04T14:30:05+01:00",
        "1.0,89.0,,",
        "2.0,,,2026-11-04T16:30:05+01:00",
        "3.0,,,",
    ]


def test_une_cellule_vide_se_relit_sans_valeur_inventee():
    relu = lire_csv(tableau_mixte(), ecrire_csv(tableau_mixte(), DONNEES))
    assert len(relu) == 4
    assert relu["heure"].tolist() == [0.0, 1.0, 2.0, 3.0]
    assert relu["act"].iloc[1] == 89.0
    assert math.isnan(relu["act"].iloc[2])
    assert relu["lot"].iloc[0] == "007"
    assert relu["lot"].iloc[1] is None
    assert relu["quand"].iloc[1] is None
    assert relu["lot"].isna().tolist() == [False, True, True, True]


def test_une_seule_colonne_garde_sa_cellule_vide():
    tableau = Tableau(
        nom="t", fichier="brut/t.csv", colonnes=(ColonneIdentifiant(nom="lot"),)
    )
    texte = ecrire_csv(tableau, {"lot": ["a", None, "c"]})
    assert lire_csv(tableau, texte)["lot"].tolist() == ["a", None, "c"]


def test_une_formule_d_incertitude_laisse_vide_la_ligne_sans_valeur():
    tableau = Tableau(
        nom="t",
        fichier="brut/t.csv",
        colonnes=(
            ColonneNumerique(nom="N", unite="1"),
            ColonneNumerique(nom="heure", unite="h", composantes=(H,)),
            ColonneIncertitude(
                nom="u_N",
                composante_de="N",
                type="B",
                unite="1",
                source="Calculée : sqrt(N)",
                formule="sqrt(N)",
            ),
        ),
    )
    relu = lire_csv(
        tableau, ecrire_csv(tableau, {"N": [100.0], "heure": [1.0, 2.0, 3.0]})
    )
    assert relu["u_N"].iloc[0] == pytest.approx(10.0)
    assert math.isnan(relu["u_N"].iloc[1])
    assert math.isnan(relu["u_N"].iloc[2])


# --- API de lecture -----------------------------------------------------------------------


def test_l_api_rend_toutes_les_colonnes_et_toutes_les_lignes(arc, page_inegale):
    p = arc.page(page_inegale)
    donnees = p.tableau("t")
    assert list(donnees.columns) == ["heure", "act", "lot", "quand"]
    assert len(donnees) == 4
    assert donnees["act"].notna().sum() == 2
    assert donnees["lot"].notna().sum() == 1
    assert p.cellule("t", "act", 1).valeur == 89.0


def test_l_api_refuse_de_donner_une_incertitude_a_une_cellule_vide(arc, page_inegale):
    with pytest.raises(ValueError, match="vide"):
        arc.page(page_inegale).cellule("t", "act", 3)


def test_une_cellule_d_incertitude_vide_ne_declare_rien(arc):
    tableau = Tableau(
        nom="t",
        fichier="brut/t.csv",
        colonnes=(
            ColonneNumerique(nom="act", unite="MBq"),
            ColonneIncertitude(
                nom="u", composante_de="act", type="B", unite="MBq", source="certificat"
            ),
        ),
    )
    identifiant = arc.nouvel_identifiant(HEURE, "p")
    arc.creer_page(
        Page(
            id=identifiant,
            titre="P",
            date_mesure=HEURE,
            date_saisie=HEURE,
            auteur="G.",
            mesures=(tableau,),
        ),
        tableaux={"t": {"act": [10.0, 20.0], "u": [0.5]}},
    )
    p = arc.page(identifiant)
    assert p.cellule("t", "act", 0).budget.incertitude_type == pytest.approx(0.5)
    assert p.cellule("t", "act", 1).budget.incertitude_type == 0.0


def test_une_colonne_calculee_plus_longue_que_le_tableau_reste_entiere(arc):
    """Avant : l'API la retirait, et le cahier coupait ses dernières lignes."""
    page = Page(
        id=arc.nouvel_identifiant(HEURE, "p"),
        titre="P",
        date_mesure=HEURE,
        date_saisie=HEURE,
        auteur="G.",
        mesures=(
            Tableau(
                nom="t",
                fichier="brut/t.csv",
                colonnes=(
                    ColonneNumerique(nom="act", unite="MBq", composantes=(MBQ,)),
                ),
            ),
        ),
    )
    arc.creer_page(page, tableaux={"t": {"act": [10.0, 20.0, 30.0]}})
    colonne = creer_colonne(
        page, "double", "t.act * 2", lambda _nom: arc.lire_tableau(page.id, "t")
    )
    page = page.model_copy(update={"mesures": (*page.mesures, colonne)})
    arc.modifier_page(page, tableaux={"t": {"act": [10.0]}})
    donnees = arc.page(page.id).tableau("t")
    assert donnees["double"].tolist() == [20.0, 40.0, 60.0]
    assert donnees["act"].iloc[0] == 10.0
    assert donnees["act"].isna().tolist() == [False, True, True]
    assert donnees.attrs["unites"]["double"] == "MBq"
    html = page_en_html(arc, page.id, SansImages())
    assert "60 ±" in html


# --- Calculs, ajustements, graphes --------------------------------------------------------


def _page_memoire() -> Page:
    return Page(
        id="2026-11-14_p_0001",
        titre="P",
        date_mesure=HEURE,
        date_saisie=HEURE,
        auteur="G.",
        mesures=(tableau_mixte(),),
    )


def _lire(nom: str) -> pd.DataFrame:
    return lire_csv(tableau_mixte(), ecrire_csv(tableau_mixte(), DONNEES))


def test_un_calcul_sur_une_cellule_vide_le_dit():
    contexte = ContexteCalcul(_page_memoire(), _lire)
    with pytest.raises(ErreurCalcul, match="vide"):
        contexte.cellule("t", "act", 3)
    assert contexte.cellule("t", "act", 2).x.x == 89.0


def test_une_colonne_calculee_reste_sans_resultat_sur_une_ligne_incomplete():
    colonne = creer_colonne(_page_memoire(), "somme", "t.act + t.act", _lire)
    assert colonne.valeurs == (200.0, 178.0, None, None)


def test_un_ajustement_ne_lit_que_les_lignes_completes():
    points = lire_points(_page_memoire(), "t", "heure", "act", _lire)
    assert points.x.valeurs.tolist() == [0.0, 1.0]
    assert points.y.valeurs.tolist() == [100.0, 89.0]


def test_un_graphe_laisse_un_trou_sans_avertissement():
    page = _page_memoire()
    donnees = preparer_graphe(
        page, {"t": _lire("t")}, ReglagesGraphe(tableau="t", x="heure", y=("act",))
    )
    serie = donnees.y[0]
    assert serie.valeurs[:2] == (100.0, 89.0)
    assert math.isnan(serie.valeurs[2])
    assert math.isnan(serie.valeurs[3])
    assert donnees.avertissements == ()


# --- Le cahier ----------------------------------------------------------------------------


def test_le_cahier_montre_toutes_les_colonnes_et_toutes_les_lignes(arc, page_inegale):
    html = page_en_html(arc, page_inegale, SansImages())
    for attendu in ("heure (h)", "act (MBq)", ">lot<", ">quand<", ">007<", ">3<"):
        assert attendu in html
    assert html.count("<tr>") >= 5
    assert "nan" not in html
    assert "None" not in html


# --- Saisie : le défaut signalé par l'utilisateur -----------------------------------------


def _deux_colonnes(espace):
    tableau = espace.ajouter_onglet("tableau")
    tableau.champ_nom.setText("t")
    for colonne, (nom, unite) in zip(tableau.colonnes, (("x", "h"), ("y", "MBq"))):
        colonne.nom.setText(nom)
        colonne.unite.setText(unite)
    return tableau


def test_une_colonne_plus_courte_est_enregistree_et_arrive_dans_le_cahier(espace):
    """Avant : une cellule vide bloquait l'enregistrement, et l'ancienne version restait."""
    tableau = _deux_colonnes(espace)
    tableau.coller("1\t10\n2\t\n3\t")
    assert tableau.erreur() is None
    assert espace.enregistrer_maintenant()
    assert espace.erreurs == []
    relu = espace.archive.lire_tableau(espace.page.id, "t")
    assert relu["x"].tolist() == [1.0, 2.0, 3.0]
    assert relu["y"].iloc[0] == 10.0
    assert relu["y"].isna().tolist() == [False, True, True]
    html = page_en_html(espace.archive, espace.page.id, SansImages())
    assert ">3<" in html


def test_ajouter_une_colonne_courte_a_un_tableau_enregistre_ne_perd_rien(espace):
    tableau = _deux_colonnes(espace)
    tableau.coller("1\t10\n2\t20\n3\t30")
    assert espace.enregistrer_maintenant()
    nouvelle = tableau.ajouter_colonne("z", "MBq")
    tableau.ecrire_cellules([["5"]], 0, tableau.colonnes.index(nouvelle))
    assert tableau.erreur() is None
    assert espace.enregistrer_maintenant()
    relu = espace.archive.lire_tableau(espace.page.id, "t")
    assert relu["z"].iloc[0] == 5.0
    assert len(relu) == 3
    html = page_en_html(espace.archive, espace.page.id, SansImages())
    assert "z (MBq)" in html


def test_une_ligne_ajoutee_a_une_seule_colonne_est_enregistree(espace):
    tableau = _deux_colonnes(espace)
    tableau.coller("1\t10\n2\t20")
    assert espace.enregistrer_maintenant()
    tableau.ecrire_cellules([["3"]], 2, 0)
    assert espace.enregistrer_maintenant()
    assert espace.archive.lire_tableau(espace.page.id, "t")["x"].tolist() == [1, 2, 3]


def test_un_tableau_inegal_se_rouvre_a_l_identique(qtbot, espace):
    tableau = _deux_colonnes(espace)
    tableau.coller("1\t10\n2\t\n3\t")
    assert espace.enregistrer_maintenant()
    identifiant = espace.page.id
    assert espace.ouvrir_page(identifiant)
    rouvert = espace.editeurs()[-1]
    assert rouvert.erreur() is None
    assert rouvert._texte(0, 1) == "10.0"
    assert rouvert._texte(1, 1) == ""
    assert rouvert._texte(2, 0) == "3.0"
    assert not rouvert.tableau_modifie()


def test_une_cellule_illisible_reste_refusee(espace):
    tableau = _deux_colonnes(espace)
    tableau.coller("1\tabc")
    assert "ligne 1" in tableau.erreur()


def test_la_page_est_ecrite_au_format_qui_annonce_les_cellules_vides(arc, page_inegale):
    assert arc.lire_page(page_inegale).version_schema == 5
