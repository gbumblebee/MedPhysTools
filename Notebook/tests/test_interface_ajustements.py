"""Vérifie l'onglet d'ajustement, sa courbe sur le graphe et son affichage dans la fiche."""

import math
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pytest
from PySide6.QtCore import Qt

from medphystools import Archive
from medphystools.ajustements import evaluer_ajustement
from medphystools.depot import Depot
from medphystools.incertitudes import Composante
from medphystools.interface.espace_saisie import DescriptionPage, EspaceSaisie
from medphystools.interface.fiche_page import FichePage
from medphystools.interface.onglet_ajustements import EditeurAjustements
from medphystools.interface.onglet_graphes import EditeurGraphes
from medphystools.interface.onglet_tableau import EditeurTableau
from medphystools.page import Ajustement, Indicateur, Tableau

HEURE = datetime(2026, 11, 14, 9, 30, tzinfo=timezone(timedelta(hours=1)))
HORLOGE = Composante(type="B", valeur=0.01, unite="h", source="horloge")
RESOLUTION = Composante(type="B", valeur=0.5, unite="MBq", source="résolution")
CERTIFICAT = Composante(
    type="B", valeur=1.0, relative=True, source="certificat", erreur_commune="CERT"
)
HEURES = [0.0, 1.0, 2.0, 3.0, 4.0, 6.0, 8.0, 10.0]
ACTIVITES = [100 * math.exp(-math.log(2) * t / 6.0) for t in HEURES]


@pytest.fixture
def arc(tmp_path):
    return Archive.creer(tmp_path / "A", "CHU X", "Resp.", date(2026, 11, 1))


@pytest.fixture
def espace(qtbot, arc, monkeypatch):
    resultat = EspaceSaisie()
    qtbot.addWidget(resultat)
    resultat.installer(arc)
    resultat.erreurs = []
    monkeypatch.setattr(resultat, "afficher_erreur", resultat.erreurs.append)
    monkeypatch.setattr(resultat, "_confirmer", lambda *_: True)
    monkeypatch.setattr(resultat, "_demander_motif", lambda: "Motif")
    assert resultat.creer_page(DescriptionPage("CQ", HEURE, "G.", None, ()))
    tableau = resultat.ajouter_onglet("tableau")
    tableau.champ_nom.setText("t")
    heure, activite = tableau.colonnes
    heure.nom.setText("heure")
    heure.unite.setText("h")
    heure.composantes = (HORLOGE,)
    activite.nom.setText("act")
    activite.unite.setText("MBq")
    activite.composantes = (RESOLUTION, CERTIFICAT)
    tableau.coller("\n".join(f"{t!r}\t{a!r}" for t, a in zip(HEURES, ACTIVITES)))
    assert resultat.enregistrer_maintenant()
    assert resultat.erreurs == []
    return resultat


@pytest.fixture
def editeur(espace):
    resultat = espace.ajouter_onglet("ajustements")
    assert isinstance(resultat, EditeurAjustements)
    return resultat


def relue(espace):
    return espace.archive.lire_page(espace.page.id)


def choisir_modele(editeur, cle):
    editeur.champ_modele.setCurrentIndex(editeur.champ_modele.findData(cle))


def ajuster(editeur, nom="fit", modele="decroissance"):
    editeur.champ_nom.setText(nom)
    choisir_modele(editeur, modele)
    assert editeur.demander_ajustement(), editeur.message.text()


def test_seules_les_colonnes_numeriques_sont_proposees(editeur):
    assert [editeur.champ_x.itemText(i) for i in range(editeur.champ_x.count())] == [
        "heure",
        "act",
    ]
    assert editeur.champ_x.currentText() == "heure"
    assert editeur.champ_y.currentText() == "act"


def test_un_modele_usuel_montre_sa_formule_et_les_unites_de_ses_coefficients(editeur):
    choisir_modele(editeur, "decroissance")
    assert editeur.champ_formule.text() == "A * exp(-ln(2) * x / T12)"
    assert editeur.champ_formule.isReadOnly()
    table = editeur.table_depart
    lignes = {
        table.item(i, 0).text(): table.item(i, 2).text()
        for i in range(table.rowCount())
    }
    assert lignes == {"A": "MBq", "T12": "h"}
    assert not table.item(0, 2).flags() & Qt.ItemFlag.ItemIsEditable


def test_un_ajustement_retrouve_les_coefficients_et_s_enregistre(espace, editeur):
    ajuster(editeur)
    assert espace.enregistrer_maintenant()
    assert espace.erreurs == []

    page = relue(espace)
    ajustement = page.grandeur("fit")
    assert isinstance(ajustement, Ajustement)
    coefficients = {p.nom: p for p in ajustement.parametres}
    assert coefficients["A"].valeur == pytest.approx(100.0, rel=1e-5)
    assert coefficients["T12"].valeur == pytest.approx(6.0, rel=1e-5)
    assert coefficients["A"].unite == "MBq"
    assert coefficients["T12"].unite == "h"
    assert ajustement.correlation[0][0] == 1.0
    assert ajustement.points == len(HEURES)

    courbe = page.grandeur("fit_courbe")
    assert isinstance(courbe, Tableau)
    donnees = espace.archive.lire_tableau(page.id, "fit_courbe")
    assert len(donnees) > len(HEURES)
    assert donnees["x"].min() == min(HEURES)
    assert donnees["x"].max() == max(HEURES)
    assert espace.archive.modifications_non_tracees() == []


def test_le_certificat_commun_passe_sur_l_amplitude(espace, editeur):
    """1 % du même certificat : A prend 1 MBq de plus, sans rien ajouter à la période."""
    ajuster(editeur)
    coefficients = {p.nom: p for p in editeur.ajustement.parametres}
    assert coefficients["A"].incertitude_type > 1.0
    assert coefficients["T12"].incertitude_type < 0.1


def test_le_resultat_laisse_sa_place_au_graphique(espace, editeur, qtbot):
    """Coefficients sur une ligne, graphique dessous, et le détail dans un autre onglet."""
    espace.resize(1300, 800)
    espace.show()
    qtbot.waitExposed(espace)
    espace.onglets.setCurrentWidget(editeur)
    ajuster(editeur)
    qtbot.wait(100)
    texte = editeur.coefficients.text()
    assert texte.startswith("A = 100 ± ")
    assert "T12 = 6 ± " in texte
    assert "χ² réduit" in texte
    assert not editeur.table_coefficients.isVisible()
    espace_graphe = editeur.toile.height()
    assert espace_graphe > 0.5 * editeur.vues.height()
    largeur = editeur.toile.width()
    editeur.bouton_reglages.setChecked(True)
    qtbot.wait(100)
    assert not editeur.reglages.isVisible()
    assert editeur.toile.width() > largeur + 200
    editeur.bouton_reglages.setChecked(False)
    assert editeur.reglages.isVisibleTo(editeur)
    editeur.vues.setCurrentIndex(1)
    qtbot.wait(50)
    assert editeur.table_coefficients.isVisible()
    assert editeur.table_correlation.isVisible()


def test_la_source_preferee_est_l_ajustement_quand_il_existe(espace, editeur):
    assert editeur.panneau.champ_source.currentData() == "points"
    ajuster(editeur)
    assert editeur.panneau.champ_source.currentData() == "fit"


def test_les_resultats_s_affichent_et_suivent_k(espace, editeur):
    ajuster(editeur)
    table = editeur.table_coefficients
    assert table.rowCount() == 2
    assert table.item(0, 0).text() == "A"
    assert table.item(0, 1).text() == "100"
    assert table.item(0, 4).text() == "MBq"
    u = editeur.ajustement.parametres[0].incertitude_type
    assert table.item(0, 2).text() == f"{2 * u:.2g}"
    assert editeur.table_correlation.columnCount() == 3
    assert editeur.table_correlation.item(0, 1).text() == "1.000"
    assert "Décroissance (période)" in editeur.resume.text()
    assert "χ² réduit" in editeur.resume.text()

    assert espace.enregistrer_maintenant()
    tete = Depot(espace.archive.racine).tete()
    editeur.champ_k.setValue(3.0)
    assert editeur.table_coefficients.item(0, 2).text() == f"{3 * u:.2g}"
    assert not espace.modifications_en_attente()
    assert Depot(espace.archive.racine).tete() == tete


def test_tout_se_retrouve_a_la_reouverture_sans_modifier_la_page(espace, editeur):
    ajuster(editeur)
    assert espace.enregistrer_maintenant()
    avant = relue(espace).grandeur("fit")
    tete = Depot(espace.archive.racine).tete()

    assert espace.ouvrir_page(espace.page.id)
    relu = espace.editeurs()[-1]
    assert isinstance(relu, EditeurAjustements)
    assert relu.ajustement == avant
    assert relu.champ_nom.text() == "fit"
    assert relu.champ_x.currentText() == "heure"
    assert relu.champ_y.currentText() == "act"
    assert relu.champ_modele.currentData() == "decroissance"
    assert relu.table_coefficients.rowCount() == 2
    assert not espace.modifications_en_attente()
    assert espace.enregistrer_maintenant()
    assert Depot(espace.archive.racine).tete() == tete
    y, u = evaluer_ajustement(relu.ajustement, [5.0])
    assert y[0] == pytest.approx(100 * math.exp(-math.log(2) * 5.0 / 6.0), rel=1e-5)
    assert u[0] > 0


def test_un_ajustement_ne_touche_a_aucune_valeur_brute(espace, editeur):
    fichier = espace.archive.racine / "pages" / espace.page.id / "brut" / "t.csv"
    avant = fichier.read_bytes()
    grandeur = relue(espace).grandeur("t")
    ajuster(editeur)
    assert calculer_indicateur(editeur, "integrale")
    assert espace.enregistrer_maintenant()
    assert fichier.read_bytes() == avant
    assert relue(espace).grandeur("t") == grandeur


def test_une_formule_personnalisee_demande_ses_unites(espace, editeur):
    editeur.champ_nom.setText("fit")
    choisir_modele(editeur, "personnalise")
    assert not editeur.champ_formule.isReadOnly()
    editeur.champ_formule.setText("A * exp(-x / tau)")
    table = editeur.table_depart
    assert [table.item(i, 0).text() for i in range(table.rowCount())] == ["A", "tau"]
    table.item(0, 1).setText("90")
    table.item(1, 1).setText("8")
    assert not editeur.demander_ajustement()
    assert "unité du coefficient « A »" in editeur.message.text()
    assert not editeur.message.isHidden()

    table.item(0, 2).setText("MBq")
    table.item(1, 2).setText("h")
    assert editeur.demander_ajustement(), editeur.message.text()
    coefficients = {p.nom: p for p in editeur.ajustement.parametres}
    assert coefficients["tau"].valeur == pytest.approx(6.0 / math.log(2), rel=1e-5)
    assert coefficients["tau"].unite == "h"
    assert editeur.ajustement.modele == "Personnalisé"
    assert editeur.message.isHidden()
    assert espace.enregistrer_maintenant()

    assert espace.ouvrir_page(espace.page.id)
    relu = espace.editeurs()[-1]
    assert relu.champ_modele.currentData() == "personnalise"
    assert relu.table_depart.item(1, 2).text() == "h"
    assert not espace.modifications_en_attente()


@pytest.mark.parametrize(
    ("formule", "attendu"),
    [
        ("open('x') * a", "fonctions"),
        ("a * 2", "dépendre de x"),
        ("a * x +", "mal écrite"),
    ],
)
def test_une_formule_incorrecte_s_explique_sans_rien_enregistrer(
    espace, editeur, formule, attendu
):
    editeur.champ_nom.setText("fit")
    choisir_modele(editeur, "personnalise")
    editeur.champ_formule.setText(formule)
    assert not editeur.demander_ajustement()
    assert editeur.ajustement is None
    assert editeur.message.text() != ""
    assert espace.enregistrer_maintenant()
    assert all(g.nom != "fit" for g in relue(espace).mesures)


def test_un_nom_deja_pris_ne_remplace_rien(espace, editeur):
    ajuster(editeur, nom="t")
    assert "le nom « t » est déjà utilisé" in espace.saisies_incompletes()[0]
    assert not espace.enregistrer_maintenant()
    assert isinstance(relue(espace).grandeur("t"), Tableau)


def test_sans_nom_un_nom_est_propose(espace, editeur):
    choisir_modele(editeur, "droite")
    assert editeur.demander_ajustement()
    assert editeur.champ_nom.text() == "ajustement"


def test_une_page_validee_demande_un_motif(espace, editeur):
    ajuster(editeur)
    assert espace.enregistrer_maintenant()
    assert espace.demander_validation()
    relu = espace.editeurs()[-1]
    choisir_modele(relu, "exponentielle")
    assert relu.demander_ajustement()
    assert espace.modifications_en_attente()
    assert not espace._minuteur.isActive()
    assert relue(espace).grandeur("fit").modele == "Décroissance (période)"
    assert espace.demander_correction()
    page = relue(espace)
    assert page.statut == "corrigée"
    assert page.grandeur("fit").modele == "Exponentielle"


def test_des_points_modifies_signalent_l_ajustement_a_refaire(espace, editeur):
    ajuster(editeur)
    assert espace.enregistrer_maintenant()
    tableau = next(e for e in espace.editeurs() if isinstance(e, EditeurTableau))
    tableau.grille.item(0, 1).setText("90")
    assert espace.enregistrer_maintenant()
    # L'ajustement reste figé, et l'onglet le dit.
    assert relue(espace).grandeur("fit").parametres == editeur.ajustement.parametres
    assert "fit : À refaire" in editeur.alerte.text()
    assert not editeur.alerte.isHidden()
    assert not espace.modifications_en_attente()
    assert editeur.demander_ajustement()
    assert espace.enregistrer_maintenant()
    assert "À refaire" not in editeur.alerte.text()


def test_retirer_l_ajustement_retire_sa_courbe_et_ses_indicateurs(espace, editeur):
    ajuster(editeur)
    assert calculer_indicateur(editeur, "moyenne", "ajustement", nom="i_fit")
    assert calculer_indicateur(editeur, "moyenne", nom="i_points")
    assert espace.enregistrer_maintenant()

    editeur.retirer_ajustement()
    assert espace.enregistrer_maintenant()
    noms = [g.nom for g in relue(espace).mesures]
    assert "fit" not in noms
    assert "fit_courbe" not in noms
    assert "i_fit" not in noms
    assert "i_points" in noms
    assert not (
        espace.archive.racine / "pages" / espace.page.id / "brut" / "fit_courbe.csv"
    ).exists()
    assert espace.erreurs == []


# --- Indicateurs -------------------------------------------------------------------------


def calculer_indicateur(editeur, methode, source="points", **champs):
    panneau = editeur.panneau
    panneau.champ_source.setCurrentIndex(
        0 if source == "points" else min(1, panneau.champ_source.count() - 1)
    )
    panneau.champ_methode.setCurrentIndex(panneau.champ_methode.findData(methode))
    for nom, valeur in champs.items():
        getattr(panneau, f"champ_{nom}").setText(valeur)
    return panneau.demander_indicateur()


def test_un_indicateur_sur_les_points_se_calcule_et_s_enregistre(espace, editeur):
    assert calculer_indicateur(editeur, "maximum", nom="pic")
    assert espace.enregistrer_maintenant()
    indicateur = relue(espace).grandeur("pic")
    assert isinstance(indicateur, Indicateur)
    assert indicateur.valeur == pytest.approx(100.0)
    assert indicateur.position == 0.0
    assert indicateur.source == "points"
    # u = √(0,5² + (1 % de 100)²), sans dépendre de la sélection de ce point.
    assert indicateur.incertitude_type == pytest.approx(math.hypot(0.5, 1.0))
    # L'abscisse suit, avec son unité et son incertitude : u(x) = 0,01 h, soit U = 0,02 h.
    assert indicateur.unite_position == "h"
    assert indicateur.incertitude_position == pytest.approx(0.01)
    assert editeur.panneau.table.item(0, 4).text() == "0 ± 0.02 h"
    assert editeur.panneau.table.item(0, 1).text() == "Maximum"
    assert editeur.panneau.table.item(0, 5).text() == "100 ± 2.2 MBq"


def test_un_indicateur_sur_l_ajustement_utilise_la_fonction(espace, editeur):
    ajuster(editeur)
    assert calculer_indicateur(
        editeur, "integrale", "ajustement", nom="aire", de="0", a="6"
    )
    # ∫ 100·2^(−t/6) dt de 0 à 6 = 100 · 6 / ln 2 · (1 − 1/2).
    attendu = 100 * 6 / math.log(2) * 0.5
    indicateur = editeur.indicateurs[0]
    assert indicateur.valeur == pytest.approx(attendu, rel=1e-5)
    assert indicateur.unite == "MBq*h"
    assert indicateur.ajustement == "fit"
    assert (indicateur.de, indicateur.a) == (0.0, 6.0)


def test_le_resultat_d_un_indicateur_s_affiche_en_entier(espace, editeur, qtbot):
    espace.resize(1300, 800)
    espace.show()
    qtbot.waitExposed(espace)
    espace.onglets.setCurrentWidget(editeur)
    ajuster(editeur)
    assert calculer_indicateur(editeur, "integrale", "ajustement", nom="aire")
    editeur.vues.setCurrentWidget(editeur.panneau)
    qtbot.wait(100)
    table = editeur.panneau.table
    texte = table.item(0, 5).text()
    assert table.columnWidth(5) >= table.fontMetrics().horizontalAdvance(texte)
    assert table.columnViewportPosition(5) + table.columnWidth(5) <= (
        table.viewport().width()
    )


def test_le_nom_d_un_indicateur_est_propose_et_unique(espace, editeur):
    assert calculer_indicateur(editeur, "moyenne")
    assert calculer_indicateur(editeur, "moyenne")
    assert [i.nom for i in editeur.indicateurs] == ["moyenne", "moyenne_2"]


def test_un_indicateur_impossible_s_explique(espace, editeur):
    assert not calculer_indicateur(editeur, "derivee")
    assert "abscisse" in editeur.panneau.message.text()
    assert not editeur.panneau.message.isHidden()
    assert not calculer_indicateur(editeur, "moyenne", "points", de="abc")
    assert "abc" in editeur.panneau.message.text()
    assert editeur.indicateurs == []


def test_un_indicateur_ajuste_refuse_d_extrapoler_a_l_ecran(espace, editeur):
    ajuster(editeur)
    assert not calculer_indicateur(editeur, "moyenne", "ajustement", de="0", a="50")
    assert "pas d'extrapolation" in editeur.panneau.message.text()


def test_retirer_un_indicateur(espace, editeur):
    assert calculer_indicateur(editeur, "moyenne", nom="m")
    assert espace.enregistrer_maintenant()
    editeur.panneau.table.setCurrentCell(0, 0)
    editeur.panneau.retirer_indicateur()
    assert espace.enregistrer_maintenant()
    assert all(g.nom != "m" for g in relue(espace).mesures)


def test_les_indicateurs_se_retrouvent_a_la_reouverture(espace, editeur):
    assert calculer_indicateur(editeur, "moyenne", nom="m", commentaire="Note")
    assert espace.enregistrer_maintenant()
    assert espace.ouvrir_page(espace.page.id)
    relu = espace.editeurs()[-1]
    assert relu.indicateurs[0].nom == "m"
    assert relu.indicateurs[0].commentaire == "Note"
    assert relu.panneau.table.item(0, 0).text() == "m"
    assert not espace.modifications_en_attente()


def test_un_coefficient_et_un_indicateur_se_calculent_dans_l_onglet_calculs(
    espace, editeur
):
    ajuster(editeur)
    assert calculer_indicateur(editeur, "moyenne", nom="m")
    assert espace.enregistrer_maintenant()
    calculs = espace.ajouter_onglet("calculs")
    noms = [calculs.insertion.itemText(i) for i in range(calculs.insertion.count())]
    assert "fit.T12" in noms
    assert "m" in noms
    assert "t.act" in noms
    assert "fit_courbe.y" not in noms
    ligne = calculs.lignes[0]
    ligne.nom.setText("lam")
    ligne.formule.setText("ln(2) / fit.T12")
    assert ligne.erreur is None
    assert ligne.resultat.valeur == pytest.approx(math.log(2) / 6.0, rel=1e-5)
    assert ligne.resultat.unite == "1/h"


# --- Graphes -----------------------------------------------------------------------------


def test_la_courbe_de_l_onglet_se_trace_avec_sa_bande(espace, editeur, qtbot):
    espace.show()
    qtbot.waitExposed(espace)
    ajuster(editeur)
    donnees = editeur.toile.donnees
    assert donnees.courbes[0].nom == "fit"
    assert not donnees.reglages.relier
    assert len(donnees.courbes[0].x) > len(HEURES)
    legende = [t.get_text() for t in editeur.toile.axes.get_legend().get_texts()]
    assert sorted(legende) == ["Ajustement fit", "act (MBq)"]
    # La bande vaut ± k·u(f) : elle suit le facteur k.
    y, u = evaluer_ajustement(editeur.ajustement, [donnees.courbes[0].x[0]])
    assert donnees.courbes[0].elargies[0] == pytest.approx(2 * u[0], rel=1e-6)
    editeur.champ_k.setValue(3.0)
    assert editeur.toile.donnees.courbes[0].elargies[0] == pytest.approx(
        3 * u[0], rel=1e-6
    )
    assert y[0] == pytest.approx(100.0, rel=1e-4)


def cocher_courbe(graphe, nom):
    for rang in range(graphe.liste_courbes.count()):
        element = graphe.liste_courbes.item(rang)
        if element.text() == nom:
            element.setCheckState(Qt.CheckState.Checked)


def test_un_onglet_de_graphes_peut_superposer_la_courbe(espace, editeur, qtbot):
    espace.show()
    qtbot.waitExposed(espace)
    ajuster(editeur)
    assert espace.enregistrer_maintenant()
    graphe = espace.ajouter_onglet("graphes")
    assert isinstance(graphe, EditeurGraphes)
    assert graphe.liste_courbes.count() == 1
    assert graphe.liste_courbes.isVisible()
    # La table de la courbe ne se propose pas comme un tableau de mesures.
    tables = [
        graphe.champ_tableau.itemText(i) for i in range(graphe.champ_tableau.count())
    ]
    assert tables == ["t"]
    cocher_courbe(graphe, "fit")
    assert graphe.toile.donnees.courbes[0].nom == "fit"
    assert espace.enregistrer_maintenant()
    assert relue(espace).onglets[-1].reglages.courbes == ("fit",)

    assert espace.ouvrir_page(espace.page.id)
    relu = espace.editeurs()[-1]
    espace.onglets.setCurrentWidget(relu)
    assert relu.toile.donnees.courbes[0].nom == "fit"
    assert not espace.modifications_en_attente()


def test_la_courbe_suit_l_unite_de_l_axe(espace, editeur, qtbot):
    espace.show()
    qtbot.waitExposed(espace)
    ajuster(editeur)
    assert espace.enregistrer_maintenant()
    calculs = espace.ajouter_onglet("calculs")
    calculs.lignes[0].nom.setText("kilo")
    calculs.lignes[0].formule.setText("t.act")
    calculs.lignes[0].unite.setText("kBq")
    assert espace.enregistrer_maintenant()
    graphe = espace.ajouter_onglet("graphes")
    graphe.liste_y.item(0).setCheckState(Qt.CheckState.Unchecked)
    for rang in range(graphe.liste_y.count()):
        element = graphe.liste_y.item(rang)
        element.setCheckState(
            Qt.CheckState.Checked
            if element.text() == "kilo"
            else Qt.CheckState.Unchecked
        )
    cocher_courbe(graphe, "fit")
    courbe = graphe.toile.donnees.courbes[0]
    assert graphe.toile.donnees.y[0].unite == "kBq"
    assert courbe.y[0] == pytest.approx(100000.0, rel=1e-4)


def test_ouvrir_une_page_n_ajuste_rien_et_ne_charge_pas_le_graphe(espace, editeur):
    ajuster(editeur)
    assert espace.enregistrer_maintenant()
    assert espace.ouvrir_page(espace.page.id)
    relu = espace.editeurs()[-1]
    assert relu.toile is None


# --- Fiche -------------------------------------------------------------------------------


@pytest.fixture
def fiche(qtbot):
    resultat = FichePage()
    qtbot.addWidget(resultat)
    return resultat


def test_la_fiche_montre_coefficients_correlation_et_indicateurs(
    espace, editeur, fiche
):
    ajuster(editeur)
    assert calculer_indicateur(editeur, "maximum", nom="pic")
    assert espace.enregistrer_maintenant()
    fiche.afficher(espace.archive, espace.page.id)
    coefficients = fiche.tables["fit.coefficients"]
    assert coefficients.item(0, 0).text() == "A"
    assert coefficients.item(0, 1).text() == "100"
    u = relue(espace).grandeur("fit").parametres[0].incertitude_type
    assert coefficients.item(0, 2).text() == f"{2 * u:.2g}"
    correlation = fiche.tables["fit.correlation"]
    assert correlation.item(1, 2).text() == "1.000"
    assert fiche.table_indicateurs.item(0, 0).text() == "pic"
    assert fiche.table_indicateurs.item(0, 5).text() == "100 ± 2.2 MBq"
    # La table de la courbe reste à part : elle ne sert qu'au tracé.
    assert "fit_courbe" not in fiche.tables
    assert "t" in fiche.tables
    assert fiche.alertes_calculs == []

    fiche.champ_k.setValue(3.0)
    assert fiche.tables["fit.coefficients"].item(0, 2).text() == f"{3 * u:.2g}"
    assert fiche.table_indicateurs.item(0, 5).text() == "100 ± 3.4 MBq"


def test_la_fiche_signale_un_ajustement_a_refaire(espace, editeur, fiche):
    ajuster(editeur)
    assert calculer_indicateur(editeur, "maximum", nom="pic")
    assert espace.enregistrer_maintenant()
    page = relue(espace)
    donnees = {
        "heure": HEURES,
        "act": [90.0, *ACTIVITES[1:]],
    }
    espace.archive.modifier_page(page, tableaux={"t": donnees})
    fiche.afficher(espace.archive, page.id)
    textes = [alerte.text() for alerte in fiche.alertes_calculs]
    assert any(t.startswith("fit : À refaire") for t in textes)
    assert any(t.startswith("pic : À refaire") for t in textes)


def test_l_api_relit_les_resultats(espace, editeur):
    ajuster(editeur)
    assert calculer_indicateur(editeur, "maximum", nom="pic")
    assert espace.enregistrer_maintenant()
    lue = Archive(espace.archive.racine).page(espace.page.id)
    assert lue.mesures["pic"].valeur == pytest.approx(100.0)
    assert lue.mesures["pic"].elargie(2) == pytest.approx(2 * math.hypot(0.5, 1.0))
    ajustement = lue.mesures["fit"]
    assert isinstance(ajustement, Ajustement)
    y, u = evaluer_ajustement(ajustement, np.array([0.0, 6.0]))
    assert y[1] == pytest.approx(50.0, rel=1e-5)
    assert np.all(u > 0)


# --- Panneau d'indicateurs dans les graphes -----------------------------------------------


def choisir_y(graphe, *noms):
    for rang in range(graphe.liste_y.count()):
        element = graphe.liste_y.item(rang)
        element.setCheckState(
            Qt.CheckState.Checked if element.text() in noms else Qt.CheckState.Unchecked
        )


@pytest.fixture
def graphe(espace, qtbot):
    resultat = espace.ajouter_onglet("graphes")
    assert isinstance(resultat, EditeurGraphes)
    return resultat


def test_un_indicateur_se_calcule_depuis_l_onglet_de_graphes(espace, graphe):
    panneau = graphe.panneau
    panneau.champ_methode.setCurrentIndex(panneau.champ_methode.findData("mediane"))
    panneau.champ_nom.setText("med")
    assert panneau.demander_indicateur(), panneau.message.text()
    assert espace.enregistrer_maintenant()
    assert espace.erreurs == []

    page = relue(espace)
    indicateur = page.grandeur("med")
    assert indicateur.valeur == pytest.approx(np.median(ACTIVITES))
    assert page.onglets[-1].indicateurs == ("med",)

    assert espace.ouvrir_page(espace.page.id)
    relu = espace.editeurs()[-1]
    assert [i.nom for i in relu.panneau.indicateurs] == ["med"]
    assert relu.panneau.table.item(0, 1).text() == "Médiane"
    assert not espace.modifications_en_attente()


def test_les_sources_du_graphe_comprennent_les_ajustements(espace, editeur, graphe):
    ajuster(editeur)
    assert espace.enregistrer_maintenant()
    panneau = graphe.panneau
    assert panneau.champ_source.findData("fit") == 1
    # Sur les points par défaut ici, contrairement à l'onglet d'ajustement.
    assert panneau.champ_source.currentData() == "points"
    panneau.champ_source.setCurrentIndex(1)
    assert panneau.champ_methode.findData("mediane") == -1
    assert panneau.champ_methode.findData("largeur_mi_hauteur") >= 0
    panneau.champ_source.setCurrentIndex(0)
    assert panneau.champ_methode.findData("largeur_mi_hauteur") == -1


def test_plusieurs_series_laissent_choisir_la_colonne(espace, graphe, qtbot):
    calculs = espace.ajouter_onglet("calculs")
    calculs.lignes[0].nom.setText("double")
    calculs.lignes[0].formule.setText("t.act * 2")
    assert espace.enregistrer_maintenant()
    espace.onglets.setCurrentWidget(graphe)
    choisir_y(graphe, "act", "double")
    panneau = graphe.panneau
    assert [panneau.champ_y.itemText(i) for i in range(panneau.champ_y.count())] == [
        "act",
        "double",
    ]
    panneau.champ_y.setCurrentText("double")
    panneau.champ_methode.setCurrentIndex(panneau.champ_methode.findData("maximum"))
    assert panneau.demander_indicateur(), panneau.message.text()
    assert panneau.indicateurs[0].y == "double"
    assert panneau.indicateurs[0].valeur == pytest.approx(200.0)


def test_un_repere_s_affiche_sur_le_graphe_a_la_demande(espace, graphe, qtbot):
    espace.show()
    qtbot.waitExposed(espace)
    espace.onglets.setCurrentWidget(graphe)
    panneau = graphe.panneau
    panneau.champ_methode.setCurrentIndex(panneau.champ_methode.findData("maximum"))
    panneau.champ_nom.setText("pic")
    assert panneau.demander_indicateur()
    assert graphe.toile.donnees.reperes == ()

    case = panneau.table.item(0, 7)
    assert case.checkState() == Qt.CheckState.Unchecked
    case.setCheckState(Qt.CheckState.Checked)
    repere = graphe.toile.donnees.reperes[0]
    assert repere.nom == "pic"
    assert (repere.x, repere.y) == pytest.approx((0.0, 100.0))
    assert espace.enregistrer_maintenant()
    assert relue(espace).grandeur("pic").afficher is True

    panneau.table.item(0, 7).setCheckState(Qt.CheckState.Unchecked)
    assert graphe.toile.donnees.reperes == ()


def test_une_moyenne_se_montre_par_une_ligne(espace, graphe, qtbot):
    espace.show()
    qtbot.waitExposed(espace)
    espace.onglets.setCurrentWidget(graphe)
    panneau = graphe.panneau
    panneau.champ_methode.setCurrentIndex(panneau.champ_methode.findData("moyenne"))
    assert panneau.demander_indicateur()
    panneau.table.item(0, 7).setCheckState(Qt.CheckState.Checked)
    repere = graphe.toile.donnees.reperes[0]
    assert repere.x is None
    assert repere.y == pytest.approx(np.mean(ACTIVITES))


def test_un_indicateur_sans_lecture_en_y_n_a_pas_de_case(espace, graphe):
    panneau = graphe.panneau
    panneau.champ_methode.setCurrentIndex(panneau.champ_methode.findData("integrale"))
    assert panneau.demander_indicateur()
    assert not panneau.table.item(0, 7).flags() & Qt.ItemFlag.ItemIsUserCheckable


def test_le_percentile_demande_son_rang_et_le_garde(espace, graphe, qtbot):
    espace.show()
    qtbot.waitExposed(espace)
    espace.onglets.setCurrentWidget(graphe)
    graphe.vues.setCurrentWidget(graphe.panneau)
    panneau = graphe.panneau
    panneau.champ_methode.setCurrentIndex(panneau.champ_methode.findData("percentile"))
    assert panneau.champ_parametre.isVisible()
    assert not panneau.champ_position.isVisible()
    assert not panneau.demander_indicateur()
    assert "rang du percentile" in panneau.message.text()
    panneau.champ_parametre.setText("90")
    assert panneau.demander_indicateur()
    assert panneau.indicateurs[0].valeur == pytest.approx(np.percentile(ACTIVITES, 90))
    assert panneau.table.item(0, 1).text() == "Percentile (90)"
    panneau.champ_methode.setCurrentIndex(panneau.champ_methode.findData("derivee"))
    assert panneau.champ_position.isVisible()
    assert not panneau.champ_parametre.isVisible()


def test_un_indicateur_sur_un_ajustement_retire_ailleurs_se_signale(
    espace, editeur, graphe
):
    ajuster(editeur)
    assert espace.enregistrer_maintenant()
    panneau = graphe.panneau
    panneau.champ_source.setCurrentIndex(1)
    panneau.champ_methode.setCurrentIndex(panneau.champ_methode.findData("moyenne"))
    assert panneau.demander_indicateur(), panneau.message.text()
    assert espace.enregistrer_maintenant()
    editeur.retirer_ajustement()
    assert espace.enregistrer_maintenant()
    assert "a été retiré" in graphe.panneau.alerte.text()
    assert not graphe.panneau.alerte.isHidden()
