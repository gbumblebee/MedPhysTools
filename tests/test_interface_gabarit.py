"""Vérifie les gabarits dans l'interface : création, nouvelle page, reste à remplir, unités."""

from datetime import date, datetime, timedelta, timezone

import pytest
from PySide6.QtCore import Qt

from pymphub import Archive
from pymphub.gabarit import (
    colonnes_choisissables,
    nom_de_gabarit,
    verifier_conformite,
    version_suivante,
)
from pymphub.incertitudes import Composante
from pymphub.interface.dialogue_gabarit import DialogueGabarit
from pymphub.interface.espace_saisie import (
    DescriptionPage,
    DialogueNouvellePage,
    EspaceSaisie,
)
from pymphub.interface.fiche_page import FichePage
from pymphub.interface.onglet_scalaires import EditeurScalaires
from pymphub.interface.onglet_tableau import EditeurTableau
from pymphub.page import Page

HEURE = datetime(2026, 11, 14, 9, 30, tzinfo=timezone(timedelta(hours=1)))
MBQ = Composante(type="B", valeur=1.0, unite="MBq", source="certificat")
HEURES = Composante(type="B", valeur=0.1, unite="h", source="horloge")


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
    return resultat


def page_source(espace):
    """Une page avec un scalaire, un identifiant et un tableau à deux colonnes."""
    assert espace.creer_page(DescriptionPage("Décroissance Tc", HEURE, "G.", None, ()))
    scalaires = espace.ajouter_onglet("scalaires")
    ligne = scalaires.lignes[0]
    ligne.nom.setText("activite")
    ligne.valeur.setText("100")
    ligne.unite.setText("MBq")
    ligne.composantes = (MBQ,)
    lot = scalaires.ajouter_ligne()
    lot.nature.setCurrentIndex(1)
    lot.nom.setText("lot")
    lot.valeur.setText("007")
    tableau = espace.ajouter_onglet("tableau")
    tableau.champ_nom.setText("t")
    heure, act = tableau.colonnes[:2]
    heure.nom.setText("heure")
    heure.unite.setText("h")
    heure.composantes = (HEURES,)
    act.nom.setText("act")
    act.unite.setText("MBq")
    act.composantes = (MBQ,)
    tableau.ecrire_cellules([["0", "100"], ["2", "80"]], 0, 0)
    assert espace.enregistrer_maintenant()
    assert espace.erreurs == []


def gabarit_cree(espace, monkeypatch=None):
    dialogue = DialogueGabarit(espace.page, espace.archive.gabarits())
    gabarit = dialogue.gabarit()
    espace.archive.enregistrer_gabarit(gabarit)
    return gabarit


def page_depuis(espace, reference):
    assert espace.creer_page(
        DescriptionPage("Suite", HEURE, "G.", None, (), gabarit=reference)
    )


def editeur_de(espace, type_):
    return next(e for e in espace.editeurs() if isinstance(e, type_))


# --- Création --------------------------------------------------------------------------


def test_le_gabarit_cree_depuis_une_page_lui_est_conforme(espace):
    page_source(espace)
    gabarit = gabarit_cree(espace)
    assert verifier_conformite(espace.page, gabarit) == []
    assert gabarit.reference == "decroissance-tc@1"
    assert espace.archive.gabarits() == ["decroissance-tc@1"]
    assert espace.page.gabarit is None


def test_le_dialogue_propose_le_nom_et_coche_tout(espace):
    page_source(espace)
    dialogue = DialogueGabarit(espace.page, [])
    assert dialogue.champ_nom.text() == "decroissance-tc"
    assert dialogue.variables() == ["activite", "lot", "t"]
    assert dialogue.bouton_creer.isEnabled()
    assert "decroissance-tc@1" in dialogue.message.text()


def test_decocher_une_colonne_ne_garde_que_les_autres(espace):
    page_source(espace)
    dialogue = DialogueGabarit(espace.page, [])
    tableau = dialogue.arbre.topLevelItem(2)
    tableau.child(0).setCheckState(0, Qt.CheckState.Unchecked)
    assert tableau.checkState(0) == Qt.CheckState.PartiallyChecked
    assert dialogue.variables() == ["activite", "lot", "t.act"]
    variable = dialogue.gabarit().variables[-1]
    assert [c.nom for c in variable.colonnes] == ["act"]


def test_rien_de_coche_ou_nom_invalide_empeche_de_creer(espace):
    page_source(espace)
    dialogue = DialogueGabarit(espace.page, [])
    dialogue.champ_nom.setText("Mon Gabarit")
    assert not dialogue.bouton_creer.isEnabled()
    dialogue.champ_nom.setText("ok")
    for rang in range(dialogue.arbre.topLevelItemCount()):
        dialogue.arbre.topLevelItem(rang).setCheckState(0, Qt.CheckState.Unchecked)
    assert not dialogue.bouton_creer.isEnabled()
    assert "Cochez" in dialogue.message.text()


def test_un_nom_deja_pris_donne_une_nouvelle_version(espace):
    page_source(espace)
    gabarit_cree(espace)
    dialogue = DialogueGabarit(espace.page, espace.archive.gabarits())
    assert dialogue.gabarit().reference == "decroissance-tc@2"


def test_le_bouton_cree_le_gabarit_sans_changer_la_page(espace, monkeypatch):
    page_source(espace)
    avant = espace.page
    choisi = DialogueGabarit(espace.page, []).gabarit()
    monkeypatch.setattr(espace, "_choisir_gabarit", lambda: choisi)
    assert espace.demander_gabarit()
    assert espace.archive.gabarits() == [choisi.reference]
    assert espace.archive.lire_page(avant.id) == avant


def test_annuler_le_dialogue_ne_cree_rien(espace, monkeypatch):
    page_source(espace)
    monkeypatch.setattr(espace, "_choisir_gabarit", lambda: None)
    assert not espace.demander_gabarit()
    assert espace.archive.gabarits() == []


def test_les_resultats_de_calcul_ne_sont_pas_proposes(espace):
    page_source(espace)
    calculs = espace.ajouter_onglet("calculs")
    calculs.lignes[0].nom.setText("double")
    calculs.lignes[0].formule.setText("activite * 2")
    assert espace.enregistrer_maintenant()
    assert "double" not in colonnes_choisissables(espace.page)
    assert "double" in {g.nom for g in espace.page.mesures}


def test_nom_et_version_se_deduisent():
    assert (
        nom_de_gabarit("Linéarité de l'activimètre !") == "linearite-de-l-activimetre"
    )
    assert version_suivante(["a@1", "a@3", "ab@9"], "a") == 4
    assert version_suivante(["ab@9"], "a") == 1


# --- Nouvelle page ---------------------------------------------------------------------


def test_le_dialogue_de_nouvelle_page_montre_ce_qui_est_attendu(espace, qtbot):
    page_source(espace)
    gabarit = gabarit_cree(espace)
    dialogue = DialogueNouvellePage(espace.archive)
    qtbot.addWidget(dialogue)
    assert dialogue.champ_gabarit.currentData() is None
    assert dialogue.attendus.isHidden()
    dialogue.champ_gabarit.setCurrentIndex(1)
    assert "activite (MBq)" in dialogue.attendus.text()
    assert "t (tableau : heure en h, act en MBq)" in dialogue.attendus.text()
    dialogue.champ_titre.setText("Suite")
    assert dialogue.description().gabarit == gabarit.reference


def test_une_page_depuis_un_gabarit_indique_ce_qu_il_reste(espace):
    page_source(espace)
    gabarit = gabarit_cree(espace)
    page_depuis(espace, gabarit.reference)
    assert espace.page.gabarit == gabarit.reference
    assert [g.nom for g in espace.page.mesures] == []
    assert espace.reste_a_remplir() == [
        "activite (MBq)",
        "lot (identifiant)",
        "t (tableau : heure en h, act en MBq)",
    ]
    assert not espace.reste.isHidden()
    assert "à remplir : activite (MBq)" in espace.reste.text()


def test_une_page_libre_n_a_pas_de_bandeau(espace):
    page_source(espace)
    assert espace.reste.isHidden()
    assert espace.reste_a_remplir() == []


def test_les_champs_attendus_sont_prepares_sans_compter_comme_saisie(espace):
    page_source(espace)
    page_depuis(espace, gabarit_cree(espace).reference)
    scalaires = editeur_de(espace, EditeurScalaires)
    assert [(ligne.nom.text(), ligne.unite.text()) for ligne in scalaires.lignes] == [
        ("activite", "MBq"),
        ("lot", ""),
    ]
    assert scalaires.lignes[1].est_identifiant()
    tableau = editeur_de(espace, EditeurTableau)
    assert tableau.champ_nom.text() == "t"
    assert [(c.nom.text(), c.unite.text()) for c in tableau.colonnes] == [
        ("heure", "h"),
        ("act", "MBq"),
    ]
    assert espace.saisies_incompletes() == []
    assert not espace.modifications_en_attente()
    assert espace.peut_quitter_la_page()


def test_les_champs_reviennent_a_la_reouverture(espace):
    page_source(espace)
    page_depuis(espace, gabarit_cree(espace).reference)
    identifiant = espace.page.id
    assert espace.ouvrir_page(identifiant)
    assert editeur_de(espace, EditeurScalaires).lignes[0].nom.text() == "activite"
    assert editeur_de(espace, EditeurTableau).champ_nom.text() == "t"


def test_remplir_vide_la_liste_et_rend_la_page_conforme(espace):
    page_source(espace)
    gabarit = gabarit_cree(espace)
    page_depuis(espace, gabarit.reference)
    scalaires = editeur_de(espace, EditeurScalaires)
    scalaires.lignes[0].valeur.setText("90")
    scalaires.lignes[0].composantes = (MBQ,)
    scalaires.lignes[0].actualiser()
    scalaires.lignes[1].valeur.setText("008")
    scalaires.ligne_modifiee()
    assert espace.enregistrer_maintenant()
    assert espace.reste_a_remplir() == ["t (tableau : heure en h, act en MBq)"]
    tableau = editeur_de(espace, EditeurTableau)
    tableau.colonnes[0].composantes = (HEURES,)
    tableau.colonnes[1].composantes = (MBQ,)
    tableau.colonne_modifiee()
    assert espace.enregistrer_maintenant()
    assert espace.reste_a_remplir() == ["t (lignes du tableau)"]
    tableau.ecrire_cellules([["0", "90"]], 0, 0)
    assert espace.enregistrer_maintenant()
    assert espace.reste_a_remplir() == []
    assert "tout est rempli" in espace.reste.text()
    assert verifier_conformite(espace.page, gabarit) == []
    assert espace.erreurs == []


def test_la_validation_est_refusee_tant_qu_il_reste_a_remplir(espace):
    page_source(espace)
    page_depuis(espace, gabarit_cree(espace).reference)
    assert not espace.demander_validation()
    assert espace.page.statut == "brouillon"
    assert "activite (MBq)" in espace.erreurs[-1]


def test_une_page_validee_n_est_pas_preparee(espace):
    page_source(espace)
    gabarit = gabarit_cree(espace)
    page_depuis(espace, gabarit.reference)
    scalaires = editeur_de(espace, EditeurScalaires)
    scalaires.lignes[0].valeur.setText("90")
    scalaires.lignes[0].composantes = (MBQ,)
    scalaires.lignes[1].valeur.setText("008")
    tableau = editeur_de(espace, EditeurTableau)
    tableau.colonnes[0].composantes = (HEURES,)
    tableau.colonnes[1].composantes = (MBQ,)
    tableau.ecrire_cellules([["0", "90"]], 0, 0)
    assert espace.enregistrer_maintenant()
    assert espace.demander_validation()
    assert espace.page.statut == "validée"
    assert len(editeur_de(espace, EditeurScalaires).lignes) == 2


def test_un_onglet_retire_n_empeche_pas_de_dire_ce_qui_manque(espace):
    page_source(espace)
    page_depuis(espace, gabarit_cree(espace).reference)
    espace.demander_retrait_onglet(
        espace.onglets.indexOf(editeur_de(espace, EditeurTableau))
    )
    assert "t (tableau : heure en h, act en MBq)" in espace.reste_a_remplir()


def test_un_gabarit_introuvable_est_signale(espace):
    page_source(espace)
    reference = gabarit_cree(espace).reference
    page_depuis(espace, reference)
    (espace.archive.racine / "gabarits" / f"{reference}.toml").unlink()
    espace.recharger()
    assert espace.reste_a_remplir() == []
    assert "introuvable" in espace.reste.text()


# --- Unités ----------------------------------------------------------------------------


def test_une_unite_incoherente_est_bloquee_a_la_saisie(espace):
    page_source(espace)
    page_depuis(espace, gabarit_cree(espace).reference)
    ligne = editeur_de(espace, EditeurScalaires).lignes[0]
    ligne.valeur.setText("90")
    ligne.unite.setText("s")
    ligne.composantes = (Composante(type="B", valeur=1.0, unite="s", source="x"),)
    ligne.actualiser()
    assert "gabarit attend MBq" in ligne.erreur()
    assert ligne.unite.property("invalide") is True
    assert any("gabarit attend MBq" in e for e in espace.saisies_incompletes())
    assert espace.enregistrer_maintenant()
    assert "activite" not in {
        g.nom for g in espace.archive.lire_page(espace.page.id).mesures
    }
    assert espace.erreurs == []


def test_une_unite_compatible_est_acceptee(espace):
    page_source(espace)
    page_depuis(espace, gabarit_cree(espace).reference)
    ligne = editeur_de(espace, EditeurScalaires).lignes[0]
    ligne.valeur.setText("2")
    ligne.unite.setText("mCi")
    ligne.composantes = (Composante(type="B", valeur=0.1, unite="mCi", source="x"),)
    ligne.actualiser()
    assert ligne.erreur() is None
    assert ligne.unite.property("invalide") is False


def test_une_colonne_d_unite_incoherente_est_bloquee(espace):
    page_source(espace)
    page_depuis(espace, gabarit_cree(espace).reference)
    tableau = editeur_de(espace, EditeurTableau)
    tableau.colonnes[1].unite.setText("s")
    assert tableau.colonnes[1].unite.property("invalide") is True
    assert "gabarit attend MBq" in tableau.erreur()
    tableau.colonnes[1].unite.setText("kBq")
    assert tableau.colonnes[1].unite.property("invalide") is False


def test_une_nature_incoherente_bloque_l_enregistrement_sans_fenetre(espace):
    page_source(espace)
    page_depuis(espace, gabarit_cree(espace).reference)
    ligne = editeur_de(espace, EditeurScalaires).lignes[0]
    ligne.nature.setCurrentIndex(1)
    ligne.valeur.setText("texte")
    ligne.actualiser()
    assert not espace.enregistrer_maintenant()
    assert any("activite" in e for e in espace.saisies_incompletes())
    assert espace.erreurs == []


# --- Onglets préparés : notes, graphes et ajustements ---------------------------------------

NOTE_METHODE = "# Méthode\nOn ajuste $A_0 e^{-t}$ sur les points."


def page_source_complete(espace):
    """La page source, avec une note, un ajustement et un graphe qui le superpose."""
    page_source(espace)
    notes = next(e for e in espace.editeurs() if e.onglet.type == "notes")
    notes.renommer("Méthode")
    notes.texte.setPlainText(NOTE_METHODE)

    tableau = editeur_de(espace, EditeurTableau)
    tableau.ecrire_cellules([["0", "100"], ["2", "80"], ["4", "61"], ["6", "42"]], 0, 0)
    assert espace.enregistrer_maintenant()

    fit = espace.ajouter_onglet("ajustements")
    fit.renommer("Ajustement")
    fit.champ_nom.setText("fit")
    assert fit.demander_ajustement(), fit.message.text()
    assert espace.enregistrer_maintenant()

    graphe = espace.ajouter_onglet("graphes")
    graphe.renommer("Courbe")
    graphe.log_y.setChecked(True)
    graphe.liste_courbes.item(0).setCheckState(Qt.CheckState.Checked)
    assert espace.enregistrer_maintenant()
    assert espace.erreurs == []
    return graphe.reglages


def remplir_tableau(espace, cellules):
    tableau = editeur_de(espace, EditeurTableau)
    tableau.colonnes[0].composantes = (HEURES,)
    tableau.colonnes[1].composantes = (MBQ,)
    tableau.colonne_modifiee()
    tableau.ecrire_cellules(cellules, 0, 0)
    return tableau


def editeur_de_type(espace, type_onglet):
    return next(e for e in espace.editeurs() if e.onglet.type == type_onglet)


def attendre_ajustement(espace, qtbot, nombre_points):
    """Attend que l'ajustement automatique soit enregistré dans la page."""

    def enregistre():
        fits = [g for g in espace.page.mesures if g.nature == "ajustement"]
        return bool(fits) and fits[0].points == nombre_points

    qtbot.waitUntil(enregistre, timeout=10000)


def test_le_dialogue_coche_les_onglets_et_les_met_dans_le_gabarit(espace):
    reglages = page_source_complete(espace)
    dialogue = DialogueGabarit(espace.page, [])
    assert dialogue.liste_onglets.topLevelItem(0).text(0) == "Méthode (notes)"
    assert len(dialogue.onglets()) == 3
    assert "3 onglet(s) déjà prêt(s)" in dialogue.message.text()
    assert dialogue.variables() == ["activite", "lot", "t"]

    gabarit = dialogue.gabarit()
    notes, fit, graphe = gabarit.onglets
    assert notes.texte == NOTE_METHODE
    assert fit.prereglage.formule == "a * x + b"
    assert graphe.reglages == reglages
    assert graphe.reglages.echelle_y == "log"
    assert graphe.reglages.courbes == ("fit",)


def test_decocher_une_colonne_utilisee_explique_quoi_faire(espace):
    page_source_complete(espace)
    dialogue = DialogueGabarit(espace.page, [])
    dialogue.arbre.topLevelItem(2).child(0).setCheckState(0, Qt.CheckState.Unchecked)
    assert not dialogue.bouton_creer.isEnabled()
    assert "t.heure" in dialogue.message.text()
    assert "décocher cet onglet" in dialogue.message.text()
    for rang in range(dialogue.liste_onglets.topLevelItemCount()):
        element = dialogue.liste_onglets.topLevelItem(rang)
        if element.text(0) != "Méthode (notes)":
            element.setCheckState(0, Qt.CheckState.Unchecked)
    assert dialogue.bouton_creer.isEnabled()


def test_un_graphe_d_une_colonne_calculee_est_grise(espace):
    page_source_complete(espace)
    calculee = {
        "nature": "colonne_calculee",
        "nom": "double",
        "formule": "2 * t.act",
        "unite": "MBq",
        "tableaux": ["t"],
        "valeurs": [1.0, 2.0, 3.0, 4.0],
        "incertitudes_types": [0.1, 0.1, 0.1, 0.1],
    }
    onglet = next(o for o in espace.page.onglets if o.type == "graphes")
    onglets = tuple(
        o.model_copy(
            update={
                "reglages": o.reglages.model_copy(update={"y": ("double",)}),
            }
        )
        if o is onglet
        else o
        for o in espace.page.onglets
    )
    page = Page.model_validate(
        espace.page.model_dump(mode="json")
        | {
            "mesures": [*espace.page.model_dump(mode="json")["mesures"], calculee],
            "onglets": [o.model_dump(mode="json") for o in onglets],
        }
    )
    dialogue = DialogueGabarit(page, [])
    element = dialogue.liste_onglets.topLevelItem(2)
    assert "non repris" in element.text(0)
    assert "double" in element.text(0)
    assert element.checkState(0) == Qt.CheckState.Unchecked
    assert not element.flags() & Qt.ItemFlag.ItemIsEnabled
    assert dialogue.bouton_creer.isEnabled()


def test_une_nouvelle_page_arrive_avec_ses_onglets_dans_l_ordre_utile(espace):
    page_source_complete(espace)
    gabarit = gabarit_cree(espace)
    page_depuis(espace, gabarit.reference)
    assert [(o.type, o.titre) for o in espace.page.onglets] == [
        ("notes", "Méthode"),
        ("scalaires", "Scalaires"),
        ("tableau", "t"),
        ("ajustements", "Ajustement"),
        ("graphes", "Courbe"),
    ]
    assert espace.page.onglets[0].texte == NOTE_METHODE
    assert editeur_de_type(espace, "notes").texte.toPlainText() == NOTE_METHODE
    # Tant que rien n'est saisi, ouvrir et regarder ne crée aucune version.
    assert not espace.modifications_en_attente()
    assert espace.page.mesures == ()


def test_le_graphe_attend_ses_donnees_puis_se_trace_tout_seul(espace, qtbot):
    page_source_complete(espace)
    gabarit = gabarit_cree(espace)
    page_depuis(espace, gabarit.reference)
    espace.show()
    graphe = editeur_de_type(espace, "graphes")
    espace.onglets.setCurrentWidget(graphe)
    qtbot.waitUntil(lambda: graphe.toile is not None)
    assert graphe.toile.donnees is None
    assert "se tracera dès que « t »" in graphe.toile.message.text()
    assert graphe.toile.barre_outils.isHidden()

    espace.onglets.setCurrentWidget(editeur_de(espace, EditeurTableau))
    remplir_tableau(espace, [["0", "100"], ["2", "80"], ["4", "61"], ["6", "42"]])
    assert espace.enregistrer_maintenant()
    espace.onglets.setCurrentWidget(graphe)
    qtbot.waitUntil(lambda: graphe.toile.donnees is not None, timeout=10000)

    assert not graphe.toile.barre_outils.isHidden()
    donnees = graphe.toile.donnees
    assert donnees.x.valeurs == (0.0, 2.0, 4.0, 6.0)
    assert donnees.y[0].valeurs == (100.0, 80.0, 61.0, 42.0)
    assert donnees.reglages.echelle_y == "log"
    assert graphe.champ_tableau.currentText() == "t"
    assert graphe.champ_x.currentText() == "heure"
    assert graphe._colonnes_y_cochees() == ("act",)
    assert graphe.log_y.isChecked()

    # L'ajustement arrive ensuite, et sa courbe se superpose sans rien toucher.
    attendre_ajustement(espace, qtbot, 4)
    qtbot.waitUntil(lambda: graphe.toile.donnees.courbes != (), timeout=10000)
    assert [c.nom for c in graphe.toile.donnees.courbes] == ["fit"]
    assert espace.erreurs == []


def test_l_ajustement_se_refait_tant_que_la_page_est_un_brouillon(espace, qtbot):
    page_source_complete(espace)
    gabarit = gabarit_cree(espace)
    page_depuis(espace, gabarit.reference)
    tableau = remplir_tableau(espace, [["0", "100"], ["2", "80"], ["4", "60"]])
    assert espace.enregistrer_maintenant()
    attendre_ajustement(espace, qtbot, 3)
    fit = espace.page.grandeur("fit")
    assert fit.parametres[0].valeur == pytest.approx(-10.0)

    tableau.ecrire_cellules([["6", "40"]], 3, 0)
    assert espace.enregistrer_maintenant()
    attendre_ajustement(espace, qtbot, 4)
    assert espace.page.grandeur("fit").parametres[0].valeur == pytest.approx(-10.0)
    assert espace.archive.modifications_non_tracees() == []
    assert espace.erreurs == []


def test_trop_peu_de_points_s_explique_sans_boucler(espace, qtbot):
    page_source_complete(espace)
    gabarit = gabarit_cree(espace)
    page_depuis(espace, gabarit.reference)
    remplir_tableau(espace, [["0", "100"]])
    assert espace.enregistrer_maintenant()
    fit = editeur_de_type(espace, "ajustements")
    qtbot.waitUntil(lambda: "il faut plus de points" in fit.message.text())
    commits = len(espace.archive._depot.commits())
    qtbot.wait(1200)
    assert len(espace.archive._depot.commits()) == commits
    assert all(g.nature != "ajustement" for g in espace.page.mesures)


def test_une_page_validee_ne_se_reajuste_jamais_toute_seule(espace, qtbot):
    page_source_complete(espace)
    gabarit = gabarit_cree(espace)
    page_depuis(espace, gabarit.reference)
    scalaires = editeur_de(espace, EditeurScalaires)
    scalaires.lignes[0].valeur.setText("90")
    scalaires.lignes[0].composantes = (MBQ,)
    scalaires.lignes[0].actualiser()
    scalaires.lignes[1].valeur.setText("008")
    scalaires.ligne_modifiee()
    tableau = remplir_tableau(espace, [["0", "100"], ["2", "80"], ["4", "60"]])
    assert espace.enregistrer_maintenant()
    attendre_ajustement(espace, qtbot, 3)
    assert espace.enregistrer_maintenant()
    assert espace.demander_validation()
    assert espace.page.statut == "validée"

    tableau = editeur_de(espace, EditeurTableau)
    tableau.ecrire_cellules([["4", "55"]], 2, 0)
    assert espace.demander_correction()
    qtbot.wait(100)
    assert espace.page.statut == "corrigée"
    # L'ancien ajustement reste figé : l'onglet demande de le refaire, sans le faire.
    fit = editeur_de_type(espace, "ajustements")
    assert espace.page.grandeur("fit").parametres[0].valeur == pytest.approx(-10.0)
    assert "À refaire" in fit.alerte.text()
    assert not espace.modifications_en_attente()


def test_retirer_l_ajustement_prepare_l_empeche_de_revenir(espace, qtbot):
    page_source_complete(espace)
    gabarit = gabarit_cree(espace)
    page_depuis(espace, gabarit.reference)
    tableau = remplir_tableau(espace, [["0", "100"], ["2", "80"], ["4", "60"]])
    assert espace.enregistrer_maintenant()
    attendre_ajustement(espace, qtbot, 3)
    fit = editeur_de_type(espace, "ajustements")
    fit.retirer_ajustement()
    assert espace.enregistrer_maintenant()
    tableau.ecrire_cellules([["6", "40"]], 3, 0)
    assert espace.enregistrer_maintenant()
    qtbot.wait(100)
    assert all(g.nature != "ajustement" for g in espace.page.mesures)
    assert (
        next(o for o in espace.page.onglets if o.type == "ajustements").prereglage
        is None
    )


def test_une_formule_personnalisee_prepare_son_depart_et_ses_unites(espace, qtbot):
    page_source_complete(espace)
    source = editeur_de_type(espace, "ajustements")
    source.champ_modele.setCurrentIndex(source.champ_modele.findData("personnalise"))
    source.champ_formule.setText("p * x + q")
    for rang, unite in ((0, "MBq/h"), (1, "MBq")):
        source.table_depart.item(rang, 2).setText(unite)
    assert source.demander_ajustement(), source.message.text()
    assert espace.enregistrer_maintenant()

    gabarit = gabarit_cree(espace)
    page_depuis(espace, gabarit.reference)
    fit = editeur_de_type(espace, "ajustements")
    assert fit.champ_modele.currentData() == "personnalise"
    assert fit.champ_formule.text() == "p * x + q"
    assert fit.table_depart.item(0, 2).text() == "MBq/h"
    assert fit.table_depart.item(0, 1).text() != ""

    remplir_tableau(espace, [["0", "100"], ["2", "80"], ["4", "60"]])
    assert espace.enregistrer_maintenant()
    attendre_ajustement(espace, qtbot, 3)
    parametres = espace.page.grandeur("fit").parametres
    assert parametres[0].valeur == pytest.approx(-10.0)
    assert parametres[0].unite == "MBq/h"


def test_le_dialogue_de_nouvelle_page_montre_les_onglets_prets(espace):
    page_source_complete(espace)
    gabarit = gabarit_cree(espace)
    dialogue = DialogueNouvellePage(espace.archive)
    dialogue.champ_gabarit.setCurrentIndex(
        dialogue.champ_gabarit.findData(gabarit.reference)
    )
    assert "Déjà prêt : Méthode, Ajustement, Courbe" in dialogue.attendus.text()


def test_un_gabarit_sans_onglets_donne_toujours_une_note_vide(espace):
    page_source(espace)
    dialogue = DialogueGabarit(espace.page, [])
    gabarit = dialogue.gabarit()
    espace.archive.enregistrer_gabarit(gabarit)
    page_depuis(espace, gabarit.reference)
    assert espace.page.onglets[0].type == "notes"
    assert espace.page.onglets[0].texte == ""


def test_la_fiche_d_une_page_preparee_sans_donnees_ne_plante_pas(qtbot, espace):
    page_source_complete(espace)
    gabarit = gabarit_cree(espace)
    page_depuis(espace, gabarit.reference)
    fiche = FichePage()
    qtbot.addWidget(fiche)
    fiche.afficher(espace.archive, espace.page.id)
    assert fiche.page.id == espace.page.id
    toile, _reglages = fiche.graphes[0]
    assert toile.donnees is None
    assert "se tracera dès que" in toile.message.text()
