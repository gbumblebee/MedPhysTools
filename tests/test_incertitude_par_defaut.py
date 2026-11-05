"""Sans incertitude déclarée, une mesure saisie reçoit 0 par défaut, et le cahier le montre."""

from datetime import date, datetime, timedelta, timezone

import pytest

from medphystools import Archive
from medphystools.export import SansImages, page_en_html
from medphystools.incertitudes import (
    SOURCE_PAR_DEFAUT,
    Composante,
    composante_exacte,
    sans_composante_par_defaut,
)
from medphystools.interface.espace_saisie import DescriptionPage, EspaceSaisie
from medphystools.interface.onglet_scalaires import TEXTE_INCERTITUDE_PAR_DEFAUT
from medphystools.page import ColonneNumerique, Tableau

HEURE = datetime(2026, 11, 14, 9, 30, tzinfo=timezone(timedelta(hours=1)))
MBQ = Composante(type="B", valeur=1.0, unite="MBq", source="certificat")


@pytest.fixture
def espace(qtbot, tmp_path, monkeypatch):
    resultat = EspaceSaisie()
    qtbot.addWidget(resultat)
    resultat.installer(
        Archive.creer(tmp_path / "A", "CHU X", "Resp.", date(2026, 11, 1))
    )
    resultat.erreurs = []
    monkeypatch.setattr(resultat, "afficher_erreur", resultat.erreurs.append)
    assert resultat.creer_page(DescriptionPage("Saisie", HEURE, "G.", None, ()))
    return resultat


def _scalaire(espace, valeur="100"):
    onglet = espace.ajouter_onglet("scalaires")
    ligne = onglet.lignes[0]
    ligne.nom.setText("activite")
    ligne.valeur.setText(valeur)
    ligne.unite.setText("MBq")
    return onglet, ligne


def _grandeur(espace, nom):
    return next(g for g in espace.page.mesures if g.nom == nom)


def test_un_scalaire_sans_incertitude_s_enregistre_a_zero(espace):
    _onglet, ligne = _scalaire(espace)
    assert ligne.erreur() is None
    assert ligne.bouton_incertitude.text() == TEXTE_INCERTITUDE_PAR_DEFAUT
    assert espace.enregistrer_maintenant()
    composantes = _grandeur(espace, "activite").composantes
    assert len(composantes) == 1
    assert composantes[0].valeur == 0.0
    assert composantes[0].unite == "MBq"
    assert composantes[0].source == SOURCE_PAR_DEFAUT
    assert _grandeur(espace, "activite").budget().incertitude_type == 0.0


def test_le_resultat_affiche_u_nulle_des_la_saisie(espace):
    _onglet, ligne = _scalaire(espace)
    ligne.afficher_resultat(2.0)
    assert ligne.resultat.text().startswith("± 0 MBq")


def test_une_ligne_sans_valeur_ni_unite_reste_incomplete(espace):
    onglet = espace.ajouter_onglet("scalaires")
    ligne = onglet.lignes[0]
    ligne.nom.setText("activite")
    assert "valeur est obligatoire" in ligne.erreur()
    ligne.valeur.setText("1")
    assert "unité est obligatoire" in ligne.erreur()


def test_une_ligne_jamais_remplie_reste_ignoree(espace):
    onglet = espace.ajouter_onglet("scalaires")
    assert onglet.lignes[0].est_vide()
    assert onglet.lignes[0].erreur() is None
    assert onglet.grandeurs() == []


def test_une_incertitude_declaree_remplace_le_defaut(espace):
    _onglet, ligne = _scalaire(espace)
    ligne.composantes = (MBQ,)
    assert espace.enregistrer_maintenant()
    assert _grandeur(espace, "activite").composantes == (MBQ,)


def test_rouvrir_une_page_ne_change_rien_et_garde_le_defaut_visible(espace):
    _scalaire(espace)
    assert espace.enregistrer_maintenant()
    avant = _grandeur(espace, "activite")
    assert espace.ouvrir_page(espace.page.id)
    ligne = espace.editeurs()[-1].lignes[0]
    assert ligne.composantes == ()
    assert ligne.bouton_incertitude.text() == TEXTE_INCERTITUDE_PAR_DEFAUT
    assert ligne.grandeur() == avant


def test_le_defaut_suit_l_unite(espace):
    _onglet, ligne = _scalaire(espace)
    ligne.unite.setText("kBq")
    assert ligne.grandeur().composantes[0].unite == "kBq"


def test_une_valeur_exacte_declaree_n_est_pas_prise_pour_le_defaut():
    exacte = (composante_exacte("MBq"),)
    assert sans_composante_par_defaut(exacte) == exacte


def _tableau(espace):
    tableau = espace.ajouter_onglet("tableau")
    tableau.champ_nom.setText("t")
    tableau.colonnes[0].nom.setText("x")
    tableau.colonnes[0].unite.setText("h")
    tableau.colonnes[1].nom.setText("y")
    tableau.colonnes[1].unite.setText("MBq")
    return tableau


def test_une_colonne_sans_incertitude_s_enregistre_a_zero(espace):
    tableau = _tableau(espace)
    tableau.coller("1\t10\n2\t20")
    assert tableau.erreur() is None
    assert tableau.colonnes[0].bouton_incertitude.text() == (
        TEXTE_INCERTITUDE_PAR_DEFAUT
    )
    assert espace.enregistrer_maintenant()
    colonne = _grandeur(espace, "t").colonnes[1]
    assert isinstance(colonne, ColonneNumerique)
    assert [c.source for c in colonne.composantes] == [SOURCE_PAR_DEFAUT]
    assert colonne.composantes[0].unite == "MBq"


def test_le_defaut_d_une_colonne_ne_revient_pas_comme_une_composante(espace):
    tableau = _tableau(espace)
    tableau.coller("1\t10")
    assert espace.enregistrer_maintenant()
    assert espace.ouvrir_page(espace.page.id)
    rouvert = espace.editeurs()[-1]
    assert rouvert.colonnes[0].composantes == ()
    assert not rouvert.tableau_modifie()
    assert rouvert.tableau_saisi() == _grandeur(espace, "t")


def test_une_colonne_d_incertitude_remplace_le_defaut(espace):
    tableau = _tableau(espace)
    u = tableau.ajouter_colonne("u_y", "MBq")
    u.nature.setCurrentIndex(u.nature.findData("incertitude"))
    u.modele_cellule = MBQ
    tableau._actualiser_cibles()
    u.cible.setCurrentText("y")
    tableau.coller("1\t10\t0.5")
    assert tableau.erreur() is None
    assert tableau.colonnes[1].bouton_incertitude.text() == "Incertitude…"
    decrit: Tableau = tableau.tableau_saisi()
    assert decrit.colonnes[1].composantes == ()
    assert [c.source for c in decrit.colonnes[0].composantes] == [SOURCE_PAR_DEFAUT]


def test_le_cahier_dit_que_l_incertitude_est_celle_par_defaut(espace):
    _scalaire(espace)
    tableau = _tableau(espace)
    tableau.coller("1\t10")
    assert espace.enregistrer_maintenant()
    html = page_en_html(espace.archive, espace.page.id, SansImages())
    assert html.count(SOURCE_PAR_DEFAUT) >= 3
