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
from pymphub.interface.onglet_scalaires import EditeurScalaires
from pymphub.interface.onglet_tableau import EditeurTableau

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
