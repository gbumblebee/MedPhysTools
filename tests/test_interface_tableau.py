"""Vérifie les tableaux : grille, collage, imports, incertitudes et mesures répétées."""

import csv
from datetime import date, datetime, timedelta, timezone

import openpyxl
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QTableWidgetItem, QTableWidgetSelectionRange

from pymphub import Archive
from pymphub.depot import Depot
from pymphub.importation import (
    Grille,
    ImportImpossible,
    charger_imports_personnalises,
    decouper_entete,
    deviner_nature,
    en_grille,
    executer_import_personnalise,
    lire_fichier,
    lire_presse_papiers,
    noms_uniques,
)
from pymphub.incertitudes import Composante
from pymphub.interface.espace_saisie import DescriptionPage, EspaceSaisie
from pymphub.interface.fiche_page import FichePage
from pymphub.interface.onglet_scalaires import EditeurScalaires
from pymphub.interface.onglet_tableau import FICHIER_IMPORTS, EditeurTableau
from pymphub.interface.texte_en_grand import (
    LIBELLE_ACTION,
    AffichageEnGrand,
    DialogueTexte,
)
from pymphub.page import Scalaire, Tableau

PARIS = timezone(timedelta(hours=1))
HEURE = datetime(2026, 11, 14, 9, 30, tzinfo=PARIS)
CERTIFICAT = Composante(type="B", valeur=1.0, unite="MBq", k=2.0, source="certificat")
HORLOGE = Composante(type="B", valeur=1.0, unite="min", source="horloge")
AFFICHEUR = Composante(type="B", valeur=0.1, unite="MBq", source="afficheur")


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
    return resultat


@pytest.fixture
def editeur(espace):
    resultat = espace.ajouter_onglet("tableau")
    assert isinstance(resultat, EditeurTableau)
    return resultat


def relue(espace):
    return espace.archive.lire_page(espace.page.id)


def commits(espace):
    return len(Depot(espace.archive.racine).commits())


def preparer(editeur, nom="decroissance"):
    """Un tableau valide : un identifiant, une heure et une activité."""
    editeur.champ_nom.setText(nom)
    while len(editeur.colonnes) < 3:
        editeur.ajouter_colonne()
    lot, heure, activite = editeur.colonnes[:3]
    lot.nom.setText("lot")
    lot.nature.setCurrentIndex(1)
    heure.nom.setText("heure")
    heure.unite.setText("h")
    heure.composantes = (HORLOGE,)
    activite.nom.setText("activite")
    activite.unite.setText("MBq")
    activite.composantes = (CERTIFICAT,)
    activite.actualiser()
    heure.actualiser()
    editeur.ecrire_cellules(
        [["007", "0", "100"], ["007", "2", "79,39"], ["10.50", "4", "63.03"]], 0, 0
    )


def cellules(editeur):
    return [
        [editeur._texte(i, j) for j in range(len(editeur.colonnes))]
        for i in editeur.lignes_remplies()
    ]


# --- Saisie et enregistrement ---------------------------------------------------------


def test_le_type_d_onglet_est_propose(espace):
    espace._remplir_menu_ajouter()
    assert "Tableau" in [a.text() for a in espace.menu_ajouter.actions()]


def test_un_tableau_vide_n_est_pas_une_erreur(espace, editeur):
    assert editeur.est_vide()
    assert espace.saisies_incompletes() == []
    assert espace.enregistrer_maintenant()
    assert relue(espace).mesures == ()


def test_un_tableau_complet_s_enregistre(espace, editeur):
    preparer(editeur)
    assert espace.saisies_incompletes() == []
    assert espace.enregistrer_maintenant()
    tableau = relue(espace).grandeur("decroissance")
    assert isinstance(tableau, Tableau)
    assert tableau.fichier == "brut/decroissance.csv"
    assert relue(espace).onglets[1].grandeurs == ("decroissance",)
    donnees = espace.archive.lire_tableau(espace.page.id, "decroissance")
    assert donnees["activite"].tolist() == [100.0, 79.39, 63.03]
    assert donnees.attrs["unites"]["heure"] == "h"


def test_une_colonne_d_identifiants_garde_le_texte_exact(espace, editeur):
    preparer(editeur)
    espace.enregistrer_maintenant()
    donnees = espace.archive.lire_tableau(espace.page.id, "decroissance")
    assert donnees["lot"].tolist() == ["007", "007", "10.50"]


def test_le_tableau_declenche_l_enregistrement_automatique(espace, editeur, qtbot):
    preparer(editeur)
    assert espace._minuteur.isActive()
    qtbot.waitUntil(lambda: bool(relue(espace).mesures), timeout=3000)


def test_le_csv_est_dans_git_et_rien_n_est_a_reprendre(espace, editeur):
    preparer(editeur)
    espace.enregistrer_maintenant()
    assert (
        f"pages/{espace.page.id}/brut/decroissance.csv"
        in Depot(espace.archive.racine).fichiers_suivis()
    )
    assert espace.archive.modifications_non_tracees() == []


def test_rouvrir_redonne_le_tableau_sans_commit(espace, editeur):
    preparer(editeur)
    espace.enregistrer_maintenant()
    avant = commits(espace)
    espace.ouvrir_page(espace.page.id)
    autre = espace.onglets.widget(1)
    assert not espace.modifications_en_attente()
    assert cellules(autre) == [
        ["007", "0.0", "100.0"],
        ["007", "2.0", "79.39"],
        ["10.50", "4.0", "63.03"],
    ]
    assert autre.colonnes[0].nature_choisie() == "identifiant"
    assert autre.colonnes[2].composantes == (CERTIFICAT,)
    espace.enregistrer_maintenant()
    assert commits(espace) == avant


def test_modifier_une_cellule_est_trace_cellule_par_cellule(espace, editeur):
    preparer(editeur)
    espace.enregistrer_maintenant()
    assert not espace.modifications_en_attente()
    editeur.ecrire_cellules([["80"]], 1, 2)
    assert espace.modifications_en_attente()
    espace.enregistrer_maintenant()
    derniere = espace.archive.historique(espace.page.id)[-1]
    champs = {m.champ: m for m in derniere.modifications}
    assert champs["mesures.decroissance.activite[2]"].ancien == "79.39"
    assert champs["mesures.decroissance.activite[2]"].nouveau == "80.0"


def test_une_ecriture_equivalente_n_est_pas_une_modification(espace, editeur):
    preparer(editeur)
    espace.enregistrer_maintenant()
    avant = commits(espace)
    editeur.ecrire_cellules([["79.390"]], 1, 2)
    assert not espace.modifications_en_attente()
    espace.enregistrer_maintenant()
    assert commits(espace) == avant


def test_une_ligne_vide_reste_toujours_disponible(editeur):
    preparer(editeur)
    assert editeur.grille.rowCount() > len(editeur.lignes_remplies())
    assert editeur.lignes_remplies() == [0, 1, 2]


def test_une_frappe_dans_la_derniere_ligne_en_ajoute_une(editeur):
    derniere = editeur.grille.rowCount() - 1
    editeur.grille.setItem(derniere, 0, QTableWidgetItem("x"))
    assert editeur.grille.rowCount() == derniere + 2


def test_supprimer_la_selection_vide_les_cellules(espace, editeur):
    preparer(editeur)
    editeur.grille.setRangeSelected(QTableWidgetSelectionRange(2, 0, 2, 2), True)
    editeur.effacer_selection()
    assert editeur.lignes_remplies() == [0, 1]


def test_renommer_le_tableau_retire_l_ancien_fichier(espace, editeur):
    preparer(editeur)
    espace.enregistrer_maintenant()
    ancien = (
        espace.archive.racine / "pages" / espace.page.id / "brut" / "decroissance.csv"
    )
    assert ancien.is_file()
    editeur.champ_nom.setText("releve")
    espace.enregistrer_maintenant()
    assert not ancien.exists()
    assert [g.nom for g in relue(espace).mesures] == ["releve"]
    assert espace.archive.lire_tableau(espace.page.id, "releve")["activite"][0] == 100.0
    assert espace.archive.modifications_non_tracees() == []


def test_retirer_une_colonne(espace, editeur):
    preparer(editeur)
    editeur.retirer_colonne(editeur.colonnes[0])
    assert espace.saisies_incompletes() == []
    espace.enregistrer_maintenant()
    assert [c.nom for c in relue(espace).grandeur("decroissance").colonnes] == [
        "heure",
        "activite",
    ]


def test_retirer_l_onglet_garde_le_tableau(espace, editeur):
    preparer(editeur)
    espace.enregistrer_maintenant()
    espace.demander_retrait_onglet(1)
    espace.enregistrer_maintenant()
    assert relue(espace).grandeur("decroissance")
    assert (
        espace.archive.lire_tableau(espace.page.id, "decroissance")["activite"][0]
        == 100.0
    )


# --- Champs obligatoires -------------------------------------------------------------


@pytest.mark.parametrize(
    ("modification", "attendu"),
    [
        (lambda e: e.champ_nom.setText(""), "nom"),
        (lambda e: e.colonnes[2].unite.setText(""), "unité"),
        (lambda e: setattr(e.colonnes[2], "composantes", ()), "incertitude"),
        (lambda e: e.colonnes[0].nom.setText(""), "nom"),
        (lambda e: e.colonnes[2].unite.setText("Mbq"), "MBq"),
        (lambda e: e.ecrire_cellules([["abc"]], 0, 2), "abc"),
        (lambda e: e.grille.item(1, 2).setText(""), "cellule vide"),
    ],
)
def test_un_tableau_incomplet_ne_s_enregistre_pas(
    espace, editeur, modification, attendu
):
    preparer(editeur)
    modification(editeur)
    assert any(attendu in erreur for erreur in espace.saisies_incompletes())
    espace.enregistrer_maintenant()
    assert relue(espace).mesures == ()
    espace._actualiser_etat()
    assert not espace.bouton_valider.isEnabled()
    assert editeur.erreur()


def test_deux_colonnes_de_meme_nom_sont_refusees(espace, editeur):
    preparer(editeur)
    editeur.colonnes[1].nom.setText("activite")
    assert any("répétées" in erreur for erreur in espace.saisies_incompletes())


def test_un_tableau_enregistre_garde_sa_derniere_version_pendant_sa_correction(
    espace, editeur
):
    preparer(editeur)
    espace.enregistrer_maintenant()
    editeur.colonnes[2].unite.setText("")
    espace.enregistrer_maintenant()
    assert relue(espace).grandeur("decroissance").colonnes[2].unite == "MBq"
    assert (
        espace.archive.lire_tableau(espace.page.id, "decroissance")["activite"][0]
        == 100.0
    )
    assert espace.modifications_en_attente()


def test_une_unite_fausse_est_signalee_et_se_corrige_avec_entree(editeur, qtbot):
    colonne = editeur.colonnes[0]
    colonne.unite.setText("Bq/ml")
    assert colonne.unite.property("invalide") is True
    assert "Bq/mL" in colonne.unite.toolTip()
    qtbot.keyClick(colonne.unite, Qt.Key.Key_Return)
    assert colonne.unite.text() == "Bq/mL"
    assert not colonne.unite.property("invalide")


def test_une_colonne_d_identifiants_n_a_ni_unite_ni_incertitude(editeur):
    colonne = editeur.colonnes[0]
    colonne.nature.setCurrentIndex(1)
    assert not colonne.unite.isEnabled()
    assert not colonne.bouton_incertitude.isEnabled()


def test_le_nom_d_un_tableau_inutilisable_est_refuse(espace, editeur):
    preparer(editeur, "mon tableau")
    assert espace.saisies_incompletes()
    espace.enregistrer_maintenant()
    assert relue(espace).mesures == ()


def test_un_nom_de_tableau_deja_pris_n_ecrase_rien(espace, editeur):
    preparer(editeur)
    espace.enregistrer_maintenant()
    autre = espace.ajouter_onglet("tableau")
    preparer(autre)
    autre.ecrire_cellules([["999"]], 0, 2)
    autre.colonnes[1].nom.setText("temps")
    espace._sur_modification()
    assert not espace.enregistrer_maintenant()
    assert any("déjà utilisé" in e for e in espace.saisies_incompletes())
    assert (
        espace.archive.lire_tableau(espace.page.id, "decroissance")["activite"][0]
        == 100.0
    )


# --- Incertitude par colonne et par cellule ------------------------------------------


def preparer_incertitude_par_cellule(editeur):
    preparer(editeur)
    editeur.colonnes[2].composantes = ()
    colonne = editeur.ajouter_colonne("u_activite")
    colonne.nature.setCurrentIndex(2)
    colonne.unite.setText("MBq")
    colonne.modele_cellule = Composante(
        type="B", valeur=0.0, unite="MBq", source="afficheur"
    )
    colonne.cible.setCurrentText("activite")
    colonne.actualiser()
    editeur.ecrire_cellules([["1"], ["0,5"], ["0.25"]], 0, 3)
    return colonne


def test_une_incertitude_par_cellule(espace, editeur):
    preparer_incertitude_par_cellule(editeur)
    assert espace.saisies_incompletes() == []
    espace.enregistrer_maintenant()
    page = espace.archive.page(espace.page.id)
    assert page.cellule("decroissance", "activite", 1).budget.incertitude_type == 0.5


def test_une_incertitude_par_cellule_s_ajoute_a_celle_de_la_colonne(espace, editeur):
    preparer_incertitude_par_cellule(editeur)
    editeur.colonnes[2].composantes = (AFFICHEUR,)
    espace.enregistrer_maintenant()
    budget = (
        espace.archive.page(espace.page.id)
        .cellule("decroissance", "activite", 0)
        .budget
    )
    assert {ligne.source for ligne in budget.lignes} == {"afficheur"}
    assert len(budget.lignes) == 2


def test_rouvrir_retrouve_la_colonne_d_incertitude(espace, editeur):
    preparer_incertitude_par_cellule(editeur)
    espace.enregistrer_maintenant()
    espace.ouvrir_page(espace.page.id)
    autre = espace.onglets.widget(1).colonnes[3]
    assert autre.nature_choisie() == "incertitude"
    assert autre.cible.currentText() == "activite"
    assert autre.modele_cellule.source == "afficheur"
    assert not espace.modifications_en_attente()


def test_une_colonne_d_incertitude_sans_loi_est_refusee(espace, editeur):
    preparer(editeur)
    colonne = editeur.ajouter_colonne("u")
    colonne.nature.setCurrentIndex(2)
    colonne.unite.setText("MBq")
    assert any("loi et la source" in e for e in espace.saisies_incompletes())


def test_le_dialogue_de_la_colonne_declare_ses_composantes(editeur, monkeypatch):
    colonne = editeur.colonnes[0]
    monkeypatch.setattr(colonne, "_demander_composantes", lambda: (CERTIFICAT,))
    colonne.bouton_incertitude.click()
    assert colonne.composantes == (CERTIFICAT,)
    assert colonne.bouton_incertitude.text() == "1 composante…"


def test_annuler_le_dialogue_ne_change_rien(editeur, monkeypatch):
    colonne = editeur.colonnes[0]
    colonne.composantes = (CERTIFICAT,)
    monkeypatch.setattr(colonne, "_demander_composantes", lambda: None)
    colonne.bouton_incertitude.click()
    assert colonne.composantes == (CERTIFICAT,)


# --- Collage -------------------------------------------------------------------------


def test_un_collage_excel_avec_virgule_decimale_donne_les_bonnes_valeurs(
    espace, editeur
):
    preparer(editeur)
    editeur.grille.setCurrentCell(3, 0)
    editeur.coller("11.5\t6\t50,04\r\n12\t8\t39,7\r\n")
    espace.enregistrer_maintenant()
    donnees = espace.archive.lire_tableau(espace.page.id, "decroissance")
    assert donnees["activite"].tolist() == [100.0, 79.39, 63.03, 50.04, 39.7]
    assert donnees["heure"].tolist() == [0.0, 2.0, 4.0, 6.0, 8.0]


def test_un_collage_garde_les_identifiants_exacts(espace, editeur):
    preparer(editeur)
    editeur.grille.setCurrentCell(3, 0)
    editeur.coller("007\t6\t50\n")
    espace.enregistrer_maintenant()
    assert (
        espace.archive.lire_tableau(espace.page.id, "decroissance")["lot"][3] == "007"
    )


def test_un_collage_part_de_la_cellule_courante(editeur):
    editeur.grille.setCurrentCell(1, 1)
    editeur.coller("a\tb\nc\td\n")
    assert editeur._texte(1, 1) == "a"
    assert editeur._texte(2, 2) == "d"


def test_un_collage_agrandit_la_grille(editeur):
    assert len(editeur.colonnes) == 2
    editeur.coller("\n".join("\t".join(["x"] * 4) for _ in range(30)))
    assert len(editeur.colonnes) == 4
    assert len(editeur.lignes_remplies()) == 30
    assert editeur.grille.rowCount() > 30


def test_un_collage_vide_ne_change_rien(editeur):
    editeur.coller("")
    assert editeur.lignes_remplies() == []


def test_ctrl_v_colle_le_presse_papiers(editeur, espace, qtbot):
    espace.resize(1280, 800)
    espace.show()
    qtbot.waitExposed(espace)
    espace.onglets.setCurrentWidget(editeur)
    espace.activateWindow()
    qtbot.waitUntil(espace.isActiveWindow, timeout=3000)
    QApplication.clipboard().setText("1\t2\n3\t4\n")
    editeur.grille.setFocus()
    editeur.grille.setCurrentCell(0, 0)
    qtbot.keyClick(editeur.grille, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
    assert cellules(editeur) == [["1", "2"], ["3", "4"]]


def test_le_bouton_coller(editeur):
    QApplication.clipboard().setText("5\t6\n")
    editeur.bouton_coller.click()
    assert cellules(editeur) == [["5", "6"]]


# --- Import ----------------------------------------------------------------------------


def ecrire_csv_test(chemin, texte, encodage="utf-8"):
    chemin.write_bytes(texte.encode(encodage))
    return chemin


def test_importer_un_csv_propose_noms_unites_et_natures(editeur, tmp_path):
    chemin = ecrire_csv_test(
        tmp_path / "releve.csv",
        "lot;Heure (h);Activité (Mbq)\n007;0;100,5\n007;2;79,39\n",
    )
    assert editeur.importer_fichier(chemin)
    noms = [c.nom.text() for c in editeur.colonnes]
    assert noms == ["lot", "Heure", "Activité"]
    assert [c.unite.text() for c in editeur.colonnes] == ["", "h", "MBq"]
    assert [c.nature_choisie() for c in editeur.colonnes] == [
        "identifiant",
        "numerique",
        "numerique",
    ]
    assert cellules(editeur) == [["007", "0", "100,5"], ["007", "2", "79,39"]]


def test_un_import_laisse_les_incertitudes_a_declarer(espace, editeur, tmp_path):
    chemin = ecrire_csv_test(tmp_path / "r.csv", "a (MBq)\n1\n2\n")
    editeur.importer_fichier(chemin)
    editeur.champ_nom.setText("t")
    assert any("incertitude" in e for e in espace.saisies_incompletes())
    editeur.colonnes[0].composantes = (CERTIFICAT,)
    editeur.colonne_modifiee()
    assert espace.saisies_incompletes() == []


def test_un_import_garde_la_trace_de_son_origine(espace, editeur, tmp_path):
    chemin = ecrire_csv_test(tmp_path / "releve.csv", "a (MBq)\n1\n2\n")
    editeur.importer_fichier(chemin)
    editeur.champ_nom.setText("t")
    editeur.colonnes[0].composantes = (CERTIFICAT,)
    espace.enregistrer_maintenant()
    assert relue(espace).grandeur("t").commentaire == "Importé depuis releve.csv"


def test_importer_un_fichier_excel(espace, editeur, tmp_path):
    chemin = tmp_path / "releve.xlsx"
    classeur = openpyxl.Workbook()
    feuille = classeur.active
    feuille.append(["lot", "activite (MBq)"])
    feuille.append(["007", 100.5])
    feuille.append(["008", 79])
    classeur.save(chemin)
    assert editeur.importer_fichier(chemin)
    editeur.champ_nom.setText("t")
    editeur.colonnes[1].composantes = (CERTIFICAT,)
    editeur.colonne_modifiee()
    espace.enregistrer_maintenant()
    donnees = espace.archive.lire_tableau(espace.page.id, "t")
    assert donnees["lot"].tolist() == ["007", "008"]
    assert donnees["activite"].tolist() == [100.5, 79.0]


def test_le_bouton_importer(editeur, tmp_path, monkeypatch):
    chemin = ecrire_csv_test(tmp_path / "r.csv", "a\n1\n")
    monkeypatch.setattr(editeur, "_choisir_fichier", lambda: str(chemin))
    editeur.bouton_importer.click()
    assert cellules(editeur) == [["1"]]


def test_annuler_le_choix_du_fichier(editeur, monkeypatch):
    monkeypatch.setattr(editeur, "_choisir_fichier", str)
    assert not editeur.demander_import()


def test_importer_sur_des_donnees_demande_confirmation(editeur, tmp_path, monkeypatch):
    preparer(editeur)
    avant = cellules(editeur)
    chemin = ecrire_csv_test(tmp_path / "r.csv", "a\n1\n")
    monkeypatch.setattr(editeur, "_confirmer_remplacement", lambda: False)
    assert not editeur.importer_fichier(chemin)
    assert cellules(editeur) == avant
    monkeypatch.setattr(editeur, "_confirmer_remplacement", lambda: True)
    assert editeur.importer_fichier(chemin)
    assert cellules(editeur) == [["1"]]


def test_un_fichier_illisible_est_explique_sans_plantage(editeur, tmp_path):
    assert not editeur.importer_fichier(tmp_path / "absent.csv")
    assert "absent.csv" in editeur.message.text()
    assert not editeur.importer_fichier(ecrire_csv_test(tmp_path / "r.pdf", "x"))
    assert ".pdf" in editeur.message.text()
    faux = ecrire_csv_test(tmp_path / "faux.xlsx", "pas un classeur")
    assert not editeur.importer_fichier(faux)
    assert "faux.xlsx" in editeur.message.text()


def test_un_fichier_vide_est_explique(editeur, tmp_path):
    assert not editeur.importer_fichier(ecrire_csv_test(tmp_path / "v.csv", ""))
    assert "vide" in editeur.message.text()


# --- Imports personnalisés ------------------------------------------------------------


@pytest.fixture
def imports(tmp_path, monkeypatch):
    dossier = tmp_path / "config"
    dossier.mkdir()
    monkeypatch.setenv("PYMPHUB_CONFIG", str(dossier))
    return dossier / FICHIER_IMPORTS


def test_un_import_personnalise_remplit_le_tableau(
    editeur, imports, tmp_path, monkeypatch
):
    imports.write_text(
        "def importer_appareil(chemin):\n"
        "    return {'lot': ['007', '008'], 'activite (MBq)': [1.5, 2.5]}\n",
        encoding="utf-8",
    )
    source = ecrire_csv_test(tmp_path / "export.dat", "x")
    monkeypatch.setattr(editeur, "_choisir_import", lambda noms: noms[0])
    monkeypatch.setattr(editeur, "_choisir_fichier", lambda: str(source))
    editeur.bouton_personnalise.click()
    assert cellules(editeur) == [["007", "1.5"], ["008", "2.5"]]
    assert editeur.colonnes[0].nature_choisie() == "identifiant"
    assert editeur.colonnes[1].unite.text() == "MBq"


@pytest.mark.parametrize(
    "code",
    [
        "def importer_a(chemin):\n    raise RuntimeError('format inconnu')\n",
        "def importer_a(chemin):\n    return 12\n",
        "def importer_a(chemin):\n    return {'a': [1], 'b': [1, 2]}\n",
        "def importer_a(chemin):\n    raise SystemExit(1)\n",
        "def importer_a():\n    return {}\n",
    ],
)
def test_une_erreur_dans_un_import_s_affiche_sans_plantage(
    editeur, imports, tmp_path, monkeypatch, code
):
    imports.write_text(code, encoding="utf-8")
    source = ecrire_csv_test(tmp_path / "e.dat", "x")
    monkeypatch.setattr(editeur, "_choisir_import", lambda noms: noms[0])
    monkeypatch.setattr(editeur, "_choisir_fichier", lambda: str(source))
    assert not editeur.demander_import_personnalise()
    assert "« a »" in editeur.message.text()
    assert editeur.lignes_remplies() == []


@pytest.mark.parametrize(
    "code",
    [
        "def importer_a(:\n",
        "import module_inexistant\n",
        "raise SystemExit(3)\n",
        "1/0\n",
    ],
)
def test_une_erreur_dans_le_fichier_d_imports_s_affiche_sans_plantage(
    editeur, imports, code
):
    imports.write_text(code, encoding="utf-8")
    assert not editeur.demander_import_personnalise()
    assert FICHIER_IMPORTS in editeur.message.text()


def test_sans_fichier_d_imports_le_message_dit_quoi_faire(editeur, imports):
    assert not editeur.demander_import_personnalise()
    assert "importer_" in editeur.message.text()
    assert FICHIER_IMPORTS in editeur.message.text()


def test_une_archive_ne_peut_pas_fournir_d_import_personnalise(
    editeur, espace, imports
):
    (espace.archive.racine / FICHIER_IMPORTS).write_text(
        "def importer_piege(chemin):\n    return {}\n", encoding="utf-8"
    )
    assert not editeur.demander_import_personnalise()
    assert "Aucun import" in editeur.message.text()


def test_ouvrir_une_archive_n_execute_aucun_import(espace, imports, tmp_path):
    temoin = tmp_path / "temoin"
    imports.write_text(f"open({str(temoin)!r}, 'w').close()\n", encoding="utf-8")
    espace.ajouter_onglet("tableau")
    espace.enregistrer_maintenant()
    espace.ouvrir_page(espace.page.id)
    assert not temoin.exists()


def test_seules_les_fonctions_d_import_sont_proposees(imports):
    imports.write_text(
        "AUTRE = 1\ndef aide():\n    pass\ndef importer_a(c):\n    pass\n",
        encoding="utf-8",
    )
    assert list(charger_imports_personnalises(imports)) == ["a"]


# --- Mesure répétée depuis une sélection ----------------------------------------------


def selectionner(editeur, ligne_debut, ligne_fin, colonne):
    editeur.grille.clearSelection()
    editeur.grille.setRangeSelected(
        QTableWidgetSelectionRange(ligne_debut, colonne, ligne_fin, colonne), True
    )


def test_la_selection_donne_la_moyenne_et_le_type_a(editeur):
    preparer(editeur)
    editeur.ecrire_cellules([["10"], ["12"], ["14"]], 0, 2)
    selectionner(editeur, 0, 2, 2)
    mesure = editeur.mesure_depuis_selection("repetee")
    assert isinstance(mesure, Scalaire)
    assert mesure.valeur == 12.0
    assert mesure.unite == "MBq"
    assert mesure.repetitions == (10.0, 12.0, 14.0)
    assert mesure.composantes[0].type == "A"
    assert mesure.composantes[0].ddl == 2
    assert mesure.composantes[1] == CERTIFICAT
    assert "decroissance" in mesure.commentaire


def test_la_mesure_rejoint_l_onglet_de_scalaires_et_la_page(
    espace, editeur, monkeypatch
):
    preparer(editeur)
    selectionner(editeur, 0, 2, 2)
    monkeypatch.setattr(
        editeur, "_demander_nom_mesure", lambda defaut: "activite_moyenne"
    )
    editeur.bouton_repetitions.click()
    espace.enregistrer_maintenant()
    page = relue(espace)
    assert page.grandeur("activite_moyenne").repetitions == (100.0, 79.39, 63.03)
    onglet_scalaires = next(o for o in page.onglets if o.type == "scalaires")
    assert onglet_scalaires.grandeurs == ("activite_moyenne",)
    assert page.grandeur("decroissance")


def test_la_mesure_rejoint_un_onglet_de_scalaires_existant(
    espace, editeur, monkeypatch
):
    scalaires = espace.ajouter_onglet("scalaires")
    assert isinstance(scalaires, EditeurScalaires)
    preparer(editeur)
    selectionner(editeur, 0, 1, 2)
    monkeypatch.setattr(editeur, "_demander_nom_mesure", lambda defaut: "m")
    editeur.demander_mesure_repetee()
    assert sum(isinstance(e, EditeurScalaires) for e in espace.editeurs()) == 1
    assert scalaires.lignes[-1].nom.text() == "m"
    assert espace.onglets.currentWidget() is scalaires


def test_le_nom_propose_vient_de_la_colonne(editeur, monkeypatch):
    preparer(editeur)
    selectionner(editeur, 0, 1, 2)
    proposes = []
    monkeypatch.setattr(editeur, "_demander_nom_mesure", lambda d: proposes.append(d))
    assert not editeur.demander_mesure_repetee()
    assert proposes == ["activite_moyenne"]


def test_les_valeurs_du_tableau_ne_changent_pas(espace, editeur, monkeypatch):
    preparer(editeur)
    espace.enregistrer_maintenant()
    avant = cellules(editeur)
    selectionner(editeur, 0, 2, 2)
    monkeypatch.setattr(editeur, "_demander_nom_mesure", lambda defaut: "m")
    editeur.demander_mesure_repetee()
    espace.enregistrer_maintenant()
    assert cellules(editeur) == avant
    assert (
        espace.archive.lire_tableau(espace.page.id, "decroissance")["activite"][0]
        == 100.0
    )


@pytest.mark.parametrize(
    ("selection", "attendu"),
    [
        ((0, 2, 0), "numérique"),
        ((0, 0, 2), "au moins deux"),
    ],
)
def test_une_selection_inutilisable_est_expliquee(editeur, selection, attendu):
    preparer(editeur)
    selectionner(editeur, *selection)
    assert not editeur.demander_mesure_repetee()
    assert attendu in editeur.message.text()


def test_une_selection_sur_deux_colonnes_est_refusee(editeur):
    preparer(editeur)
    editeur.grille.setRangeSelected(QTableWidgetSelectionRange(0, 1, 1, 2), True)
    assert not editeur.demander_mesure_repetee()
    assert "une seule colonne" in editeur.message.text()


# --- Page validée et fichier abîmé ----------------------------------------------------


def test_corriger_une_cellule_validee_exige_un_motif(espace, editeur):
    preparer(editeur)
    assert espace.demander_validation()
    autre = espace.onglets.widget(1)
    autre.ecrire_cellules([["80"]], 1, 2)
    assert espace.modifications_en_attente()
    assert not espace._minuteur.isActive()
    espace.enregistrer_maintenant()
    assert (
        espace.archive.lire_tableau(espace.page.id, "decroissance")["activite"][1]
        == 79.39
    )
    assert espace.demander_correction()
    assert (
        espace.archive.lire_tableau(espace.page.id, "decroissance")["activite"][1]
        == 80.0
    )
    correction = espace.archive.corrections(espace.page.id)[-1]
    assert correction.motif == "Motif"
    assert "mesures.decroissance.activite[2]" in {
        m.champ for m in correction.modifications
    }


def test_un_csv_illisible_est_signale_et_jamais_ecrase(espace, editeur):
    preparer(editeur)
    espace.enregistrer_maintenant()
    chemin = (
        espace.archive.racine / "pages" / espace.page.id / "brut" / "decroissance.csv"
    )
    chemin.write_text("colonnes,differentes\n1,2\n", encoding="utf-8")
    espace.ouvrir_page(espace.page.id)
    autre = espace.onglets.widget(1)
    assert "illisible" in autre.message.text()
    assert espace.saisies_incompletes()
    espace.enregistrer_maintenant()
    assert chemin.read_text(encoding="utf-8") == "colonnes,differentes\n1,2\n"


def test_la_fiche_affiche_le_tableau_saisi(espace, editeur, qtbot):
    from pymphub.interface.fiche_page import FichePage

    preparer(editeur)
    espace.enregistrer_maintenant()
    fiche = FichePage()
    qtbot.addWidget(fiche)
    fiche.afficher(espace.archive, espace.page.id)
    assert fiche.tables["decroissance"].rowCount() == 3


# --- Cœur : lecture des données extérieures -------------------------------------------


def test_presse_papiers_excel():
    assert lire_presse_papiers("a\tb\r\n1,5\t2\r\n") == [["a", "b"], ["1,5", "2"]]


def test_presse_papiers_lignes_inegales_et_fin_vide():
    assert lire_presse_papiers("a\nb\tc\n\n\n") == [["a", ""], ["b", "c"]]


def test_presse_papiers_vide():
    assert lire_presse_papiers("") == []


@pytest.mark.parametrize(
    ("texte", "attendu"),
    [
        ("a;b\n1,5;2\n", [["a", "b"], ["1,5", "2"]]),
        ("a\tb\n1,5\t2\n", [["a", "b"], ["1,5", "2"]]),
        ("a,b\n1.5,2\n", [["a", "b"], ["1.5", "2"]]),
        ("﻿a;b\n1;2\n", [["a", "b"], ["1", "2"]]),
    ],
)
def test_lire_un_fichier_csv_detecte_le_separateur(tmp_path, texte, attendu):
    assert lire_fichier(ecrire_csv_test(tmp_path / "f.csv", texte)) == attendu


def test_lire_un_fichier_windows_ancien(tmp_path):
    chemin = ecrire_csv_test(tmp_path / "f.csv", "activité;é\n1;2\n", "cp1252")
    assert lire_fichier(chemin)[0] == ["activité", "é"]


def test_lire_un_csv_avec_guillemets(tmp_path):
    chemin = tmp_path / "f.csv"
    with open(chemin, "w", newline="", encoding="utf-8") as fichier:
        csv.writer(fichier, delimiter=";").writerows([["a;b", "c"], ["1", "2"]])
    assert lire_fichier(chemin)[0] == ["a;b", "c"]


def test_lire_un_classeur_conserve_le_texte(tmp_path):
    chemin = tmp_path / "f.xlsx"
    classeur = openpyxl.Workbook()
    classeur.active.append(["007", 1.5, 2, None, datetime(2026, 1, 2, 3, 4)])
    classeur.save(chemin)
    assert lire_fichier(chemin) == [["007", "1.5", "2", "", "2026-01-02T03:04:00"]]


def test_une_premiere_ligne_sans_nombre_est_un_en_tete():
    grille = en_grille([["a", "b"], ["1", "2"]])
    assert grille == Grille(["a", "b"], [["1", "2"]])


def test_une_premiere_ligne_de_nombres_est_une_donnee():
    grille = en_grille([["1", "2"], ["3", "4"]])
    assert grille.entetes == ["", ""]
    assert grille.lignes == [["1", "2"], ["3", "4"]]


def test_une_grille_vide_est_refusee():
    with pytest.raises(ImportImpossible):
        en_grille([])


@pytest.mark.parametrize(
    ("entete", "attendu"),
    [
        ("Activité (MBq)", ("Activité", "MBq")),
        ("activite [Mbq]", ("activite", "MBq")),
        ("Concentration (Bq/ml)", ("Concentration", "Bq/mL")),
        ("Temps", ("Temps", "")),
        ("Valeur (inconnue)", ("Valeur_inconnue", "")),
        ("", ("colonne_4", "")),
        ("!!!", ("colonne_4", "")),
    ],
)
def test_decouper_un_en_tete(entete, attendu):
    assert decouper_entete(entete, 3) == attendu


def test_noms_uniques():
    assert noms_uniques(["a", "b", "a", "a"]) == ["a", "b", "a_2", "a_3"]


@pytest.mark.parametrize(
    ("valeurs", "attendu"),
    [
        (["1", "2,5", "1e3"], "numerique"),
        (["0,5", "0.25", "0"], "numerique"),
        (["-0.5"], "numerique"),
        (["007", "008"], "identifiant"),
        (["1", "abc"], "identifiant"),
        (["10", "010"], "identifiant"),
        ([], "numerique"),
        (["", ""], "numerique"),
        (["1", ""], "numerique"),
    ],
)
def test_deviner_la_nature_d_une_colonne(valeurs, attendu):
    assert deviner_nature(valeurs) == attendu


def test_un_import_peut_renvoyer_des_lignes():
    grille = executer_import_personnalise(
        "a", lambda _chemin: [["x", "y"], [1, 2.5], [3, None]], None
    )
    assert grille.entetes == ["x", "y"]
    assert grille.lignes == [["1", "2.5"], ["3", ""]]


def test_un_import_peut_renvoyer_une_grille():
    attendue = Grille(["a"], [["1"]])
    assert executer_import_personnalise("a", lambda _chemin: attendue, None) == attendue


# --- Commentaire d'une colonne --------------------------------------------------------


def tableau_commente(editeur):
    editeur.champ_nom.setText("releves")
    numerique, identifiant = editeur.colonnes
    numerique.nom.setText("activite")
    numerique.unite.setText("MBq")
    numerique.composantes = (CERTIFICAT,)
    numerique.commentaire.setText("Lue sur l'afficheur")
    identifiant.nom.setText("lot")
    identifiant.nature.setCurrentIndex(1)
    identifiant.commentaire.setText("Numéro du flacon")
    editeur.coller("10\tA\n20\tB")


def test_chaque_colonne_garde_son_commentaire(espace, editeur):
    tableau_commente(editeur)
    assert espace.enregistrer_maintenant()

    tableau = espace.archive.lire_page(espace.page.id).grandeur("releves")
    assert [c.commentaire for c in tableau.colonnes] == [
        "Lue sur l'afficheur",
        "Numéro du flacon",
    ]

    espace.ouvrir_page(espace.page.id)
    relu = espace.editeurs()[-1]
    assert [c.commentaire.text() for c in relu.colonnes] == [
        "Lue sur l'afficheur",
        "Numéro du flacon",
    ]
    assert relu.colonnes[0].commentaire.toolTip() == "<p>Lue sur l&#x27;afficheur</p>"


def test_une_colonne_d_incertitude_garde_son_commentaire(espace, editeur):
    editeur.champ_nom.setText("releves")
    valeur, incertitude = editeur.colonnes
    valeur.nom.setText("activite")
    valeur.unite.setText("MBq")
    incertitude.nom.setText("u_activite")
    incertitude.nature.setCurrentIndex(2)
    incertitude.unite.setText("MBq")
    incertitude.modele_cellule = CERTIFICAT
    incertitude.cible.setCurrentText("activite")
    incertitude.commentaire.setText("Certificat du jour")
    editeur.coller("10\t1")
    assert espace.enregistrer_maintenant()

    espace.ouvrir_page(espace.page.id)
    assert espace.editeurs()[-1].colonnes[1].commentaire.text() == "Certificat du jour"


def test_changer_un_commentaire_de_colonne_s_enregistre_sans_toucher_au_csv(
    espace, editeur
):
    tableau_commente(editeur)
    assert espace.enregistrer_maintenant()
    fichier = espace.archive.racine / "pages" / espace.page.id / "brut" / "releves.csv"
    avant = fichier.read_bytes()

    editeur.colonnes[0].commentaire.setText("Relue deux fois")
    assert espace.modifications_en_attente()
    assert espace.enregistrer_maintenant()

    assert fichier.read_bytes() == avant
    historique = espace.archive.historique(espace.page.id)[-1]
    assert [m.champ for m in historique.modifications] == [
        "mesures.releves.colonnes.activite.commentaire"
    ]


def test_un_commentaire_de_colonne_se_retrouve_par_la_recherche(espace, editeur):
    tableau_commente(editeur)
    assert espace.enregistrer_maintenant()
    assert espace.archive.chercher_identifiants(texte="afficheur") == [espace.page.id]


def test_la_fiche_affiche_les_commentaires_de_colonnes(espace, editeur, qtbot):
    tableau_commente(editeur)
    assert espace.enregistrer_maintenant()
    fiche = FichePage()
    qtbot.addWidget(fiche)
    fiche.afficher(espace.archive, espace.page.id)

    assert (
        fiche.commentaires_colonnes["releves.activite"].text()
        == "activite : Lue sur l'afficheur"
    )
    assert fiche.commentaires_colonnes["releves.lot"].text() == "lot : Numéro du flacon"
    assert (
        fiche.tables["releves"].horizontalHeaderItem(0).toolTip()
        == "<p>Lue sur l&#x27;afficheur</p>"
    )


def test_le_commentaire_d_une_colonne_s_affiche_en_grand_dans_la_fiche(
    espace, editeur, qtbot, monkeypatch
):
    tableau_commente(editeur)
    assert espace.enregistrer_maintenant()
    fiche = FichePage()
    qtbot.addWidget(fiche)
    fiche.afficher(espace.archive, espace.page.id)
    ouverts = []
    monkeypatch.setattr(
        DialogueTexte,
        "exec",
        lambda d: ouverts.append((d.windowTitle(), d.texte.toPlainText())),
    )
    etiquette = fiche.commentaires_colonnes["releves.activite"]
    menu = etiquette.findChild(AffichageEnGrand).menu()
    next(a for a in menu.actions() if a.text() == LIBELLE_ACTION).trigger()
    assert ouverts == [("Commentaire de releves.activite", "Lue sur l'afficheur")]
